# Runbook: host Necromancer on one EC2 instance until 29 Jan 2027

Nothing here has been launched. The commands are for you to run, in order. They create AWS resources
(a role, a security group, an instance, an Elastic IP) that bill until you delete them (section 12).
The reasoning and the evidence are in `deploy/DECISIONS.md`. Prices: `.agents/tasks/hosting-kit/prices.md`
(read 25 Nov 2026, us-east-1).

Decide first: the instance type (default `t4g.small`, about $37 until 29 Jan; `t3.small` about $44, same
steps with `x86_64` in step 3) and the hostname (your own domain, or a free `<ip>.sslip.io` name).
Also decide whether a hosted URL is needed at all: the Devpost rule is still being checked (see the spec, T7.8).

All commands run on your laptop with the `default` AWS profile unless marked **(on the instance)**.

## 1. IAM role for the instance (Bedrock only)

The policy allows `bedrock:InvokeModel` on the Nova 2.5 Sonic foundation model in us-east-1 and nothing
else (that single action also covers the bidirectional stream API).

```bash
aws iam create-role --role-name necromancer-ec2 \
  --assume-role-policy-document file://deploy/iam/ec2-trust-policy.json
aws iam put-role-policy --role-name necromancer-ec2 --policy-name bedrock-nova-sonic \
  --policy-document file://deploy/iam/bedrock-nova-sonic-policy.json
aws iam create-instance-profile --instance-profile-name necromancer-ec2
aws iam add-role-to-instance-profile --instance-profile-name necromancer-ec2 --role-name necromancer-ec2
```

## 2. Security group (HTTPS in, SSH from your address only)

```bash
export AWS_REGION=us-east-1
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 create-security-group --group-name necromancer --description "Necromancer web" \
  --vpc-id "$VPC" --query GroupId --output text)
MYIP=$(curl -s https://checkip.amazonaws.com)
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 443 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "$MYIP/32"
echo "$SG"
```

Rules, and why: 443 from anywhere (browsers). 22 only from your current address; if your address changes,
replace the rule (`revoke-security-group-ingress`, then authorize again). Port 8765 is not opened: the app
listens on 127.0.0.1 only and Caddy is the only public listener. Port 80 is not opened: Caddy gets its
certificate over 443 (TLS-ALPN challenge). The cost is that `http://host` does not redirect, so give people
the `https://` link. If issuance fails, open 80 temporarily (`--port 80 --cidr 0.0.0.0/0`).
All outbound traffic stays allowed (package installs, Let's Encrypt, Wayback, Bedrock).

## 3. Launch (IMDSv2 required, encrypted 16 GB disk)

```bash
aws ec2 create-key-pair --key-name necromancer --query KeyMaterial --output text > necromancer.pem
chmod 400 necromancer.pem        # *.pem is gitignored; never commit it
AMI=$(aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
  --query Parameter.Value --output text)     # for t3.small use ...-kernel-default-x86_64
aws ec2 run-instances --image-id "$AMI" --instance-type t4g.small --key-name necromancer \
  --security-group-ids "$SG" --iam-instance-profile Name=necromancer-ec2 \
  --metadata-options HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=1 \
  --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=16,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=necromancer}]' \
  --user-data file://deploy/user-data.sh --query 'Instances[0].InstanceId' --output text
```

`user-data.sh` clones the `main` branch. Merge the hosting kit to `main` and push first, or edit `REPO_REF` in `user-data.sh`.
A new instance profile can take a minute to become usable; if `run-instances` complains about the profile, wait and retry.
`user-data.sh` clones the public repo and runs `deploy/bootstrap.sh`, which takes a few minutes. Follow it
**(on the instance)** with `sudo tail -f /var/log/cloud-init-output.log` until "Bootstrap done".

## 4. Stable address and name

```bash
INSTANCE=<instance id from step 3>
ALLOC=$(aws ec2 allocate-address --domain vpc --query AllocationId --output text)
aws ec2 associate-address --instance-id "$INSTANCE" --allocation-id "$ALLOC"
aws ec2 describe-addresses --allocation-ids "$ALLOC" --query 'Addresses[0].PublicIp' --output text
```

Own domain: create an A record `necro.<yourdomain>` pointing at that IP. No domain: use
`<ip with dashes>.sslip.io`, for example `203-0-113-10.sslip.io` (third-party DNS; Let's Encrypt rate limits
on shared names were not verified, so prefer your own domain).

## 5. Copy the Enron index (973 MiB) and the Wayback cache

The privacy note in `README.md` applies: the index holds real people's email. Copy it only to this
instance (encrypted disk). Do not put it in a public bucket or in the repo.

```bash
IP=<elastic ip>
sha256sum data/enron/enron.sqlite
rsync -avP --partial -e "ssh -i necromancer.pem" data/enron/enron.sqlite ec2-user@$IP:enron.sqlite
rsync -av -e "ssh -i necromancer.pem" data/wayback/ ec2-user@$IP:wayback/
ssh -i necromancer.pem ec2-user@$IP
```

**(on the instance)**
```bash
sha256sum ~/enron.sqlite          # must equal the laptop value
sudo install -o necro -g necro -m 640 ~/enron.sqlite /opt/necromancer/data/enron/enron.sqlite
sudo cp -r ~/wayback/. /opt/necromancer/data/wayback/ && sudo chown -R necro:necro /opt/necromancer/data/wayback
rm ~/enron.sqlite
```

Alternative if the laptop copy is lost: rebuild on the instance with the README commands (download the 443 MB
CMU archive, `extract`, `ingest`, about 12 minutes on a dev machine; not measured on a t4g.small and it needs
about 3 GB of temporary disk).
To refresh the Wayback cache instead of copying it (be polite to archive.org; it may throttle cloud addresses):
```bash
sudo -u necro /opt/necromancer/.venv/bin/python /opt/necromancer/scripts/prewarm_wayback.py \
  --site http://www.pets.com/ --timestamp 19991101000000
sudo -u necro /opt/necromancer/.venv/bin/python /opt/necromancer/scripts/prewarm_wayback.py \
  --site http://www.altavista.com/ --timestamp 19971101000000
```

## 6. Access code and settings **(on the instance)**

```bash
sudo /opt/necromancer/deploy/set_access_code.sh --no-restart          # public code, printed once; copy it
sudo /opt/necromancer/deploy/set_access_code.sh --judge --no-restart  # JUDGE code, printed once; copy it into the Devpost form only
sudo nano /etc/necromancer/necromancer.env                       # check caps and NECRO_DEMO_VIDEO_URL
echo "NECRO_SITE=necro.example.com" | sudo tee /etc/necromancer/caddy.env     # your real hostname
```

The code and limits live only in `/etc/necromancer/necromancer.env` (root, mode 600), never in the repo.
Set the caps from the measured price once T7.5 is done. Judges use their own code and allowance
(`NECRO_JUDGE_*`); the public caps never block them. The judge code expires at `NECRO_JUDGE_EXPIRES`.
Keep `NECRO_TRUST_PROXY=1` so one visitor's wrong guesses cannot lock out the judges.

## 7. Start **(on the instance)**

```bash
sudo systemctl start necromancer          # warms the index first (a few seconds), then serves on 127.0.0.1:8765
sudo systemctl start caddy                # fetches the certificate on first start
systemctl status necromancer caddy --no-pager
journalctl -u caddy -n 30 --no-pager      # look for "certificate obtained successfully"
```

## 8. Smoke test (no billed calls) from your laptop

```bash
NECRO_SMOKE_CODE=<the code> .venv/bin/python deploy/smoke_test.py https://necro.example.com --app-port-closed $IP:8765
```
All lines must say PASS. It checks the page, the Origin check, a wrong code, the right code, and the
paused/caps state, and that 8765 is not reachable. It never opens `/io`, so it cannot start a billed session.

## 9. First real voice session (billed, once, by you)

Open `https://necro.example.com`, enter the code, allow the microphone, start voice and say one sentence.
Then **(on the instance)**: `tail -n 3 /opt/necromancer/var/usage.jsonl` (this is the T7.5 measurement) and
`journalctl -u necromancer -n 30 --no-pager`. If Bedrock answers AccessDenied, the role is missing
`bedrock:InvokeModel` for `amazon.nova-2-5-sonic` in us-east-1, or the Bedrock model access is not enabled.

## 10. Watch and pause **(on the instance)**

```bash
cat /opt/necromancer/var/caps.json                      # sessions used today and in total
tail -n 5 /opt/necromancer/var/usage.jsonl
sudo systemctl stop necromancer                         # pause everything now (the site shows an error page)
sudo /opt/necromancer/deploy/set_access_code.sh         # new public code; the old one stops working at once
sudo -u necro touch /opt/necromancer/var/control/disable-public   # cut off the PUBLIC code now, no restart
sudo -u necro touch /opt/necromancer/var/control/disable-judge    # cut off the JUDGE code now (only if it leaked)
sudo rm /opt/necromancer/var/control/disable-public               # switch it back on
```

## 11. Update and roll back **(on the instance)**

```bash
sudo /opt/necromancer/deploy/update.sh <commit-or-tag>   # deploy; remembers the previous commit
sudo /opt/necromancer/deploy/update.sh --rollback        # return to the previous commit
```
Both keep the index, `var/` and the env files. Re-run the smoke test after either.

## 12. Shut down after the judging window (29 Jan 2027) from your laptop

Billing stops only when these are deleted (a stopped instance still bills its disk and IP).

```bash
aws ec2 terminate-instances --instance-ids "$INSTANCE"
aws ec2 wait instance-terminated --instance-ids "$INSTANCE"
aws ec2 release-address --allocation-id "$ALLOC"
aws ec2 delete-security-group --group-id "$SG"
aws ec2 delete-key-pair --key-name necromancer && rm -f necromancer.pem
aws iam remove-role-from-instance-profile --instance-profile-name necromancer-ec2 --role-name necromancer-ec2
aws iam delete-instance-profile --instance-profile-name necromancer-ec2
aws iam delete-role-policy --role-name necromancer-ec2 --policy-name bedrock-nova-sonic
aws iam delete-role --role-name necromancer-ec2
```
Then check Billing the next day. To stop only voice spending but keep the page up, use section 10 instead.
