# Hosting decisions and evidence (T7.8, T7.9)

Date of the research: 25 Nov 2026. Nothing was launched or changed in AWS. Read-only AWS calls only
(`pricing get-products`, `bedrock list-foundation-models`, `iam simulate-custom-policy`).
Numbers from the earlier measurement step are in `.agents/tasks/hosting-kit/{measurement,prices,arm-check}.md`
(not committed; the figures are copied into the spec under T7.9).

## 1. Host

| Option | Cost to 29 Jan 2027 (66 days, 16 GB gp3, one IPv4) | Notes |
|---|---|---|
| t4g.small (Graviton, 2 GiB) | $37.31 | Every pinned package has a Python 3.12 aarch64 wheel or is pure Python. Not run on a real Graviton host yet. |
| t3.small (x86, 2 GiB) | $43.64 | Same steps with the x86_64 AMI. |
| t3.medium (4 GiB) | $76.59 | Not needed (see memory below). |
| Lightsail 2 GB | about $26 to $36 | No instance role: needs long-lived IAM keys on the box. Per-hour billing unverified. |

Memory: the server peaked at 112 to 116 MiB RSS with the real 973 MiB index (5 browsers, 30 parallel searches,
12 start/stop rounds, no leak). With the instance role read from IMDS there is no AWS CLI child process (that
child needed 63 MiB and 0.6 s per stream). The index adds 973 MiB of reclaimable page cache. About 900 MiB of
2,048 MiB stays free by that arithmetic; the OS footprint on a real instance was not measured.

Recommendation: **t4g.small**, launched and smoke-tested well before 16 Dec. If the aarch64 install or the
smoke test fails, relaunch as t3.small with the x86_64 AMI ($6.33 more). Free-tier promotions and CPU-credit
surplus charges were not checked; `t4g` and `t3` default to unlimited credit mode, so watch the bill if CPU
stays high. Bedrock usage is on top of all figures.

## 2. Credentials on EC2 (item 4)

`voice/credentials.py` now has two sources, chosen by `NECRO_CREDENTIALS_SOURCE`:
`cli` is the old `aws configure export-credentials` flow; `imds` reads the instance role from IMDSv2
(PUT token, then the role name, then the credentials; no IMDSv1 fallback), caches them and refreshes when
fewer than 5 minutes remain; `auto` (default) tries `cli` first and falls back to `imds`, so a laptop behaves as before.
The service sets `imds`. Secrets are never logged and error messages carry no response bodies.
Tests use a fake metadata endpoint (`tests/test_voice_credentials.py`). Not tested: a real EC2 instance.

IAM policy `deploy/iam/bedrock-nova-sonic-policy.json`: action `bedrock:InvokeModel`, resource
`arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-5-sonic`.
Evidence:
- The Service Authorization Reference for Amazon Bedrock lists the operation `InvokeModelWithBidirectionalStream`
  as requiring the IAM action `bedrock:InvokeModel` (there is no separate bidirectional action):
  <https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonbedrock.html>.
- The same page defines the `foundation-model` resource ARN as `arn:${Partition}:bedrock:${Region}::foundation-model/${ResourceId}`
  and lists it as a valid resource type for `InvokeModel`. The account field is empty, so no account id is needed.
- `aws bedrock list-foundation-models --region us-east-1` returned `amazon.nova-2-5-sonic` with inference type
  ON_DEMAND and exactly that ARN, so the model is called directly and not through an inference profile.
- `aws iam simulate-custom-policy` was tried and gave no usable answer (it echoed a placeholder resource and
  returned implicitDeny), so it is **not** evidence. The real check is runbook step 9, the first billed session.
  If Bedrock answers AccessDenied there, widen the resource to `arn:aws:bedrock:us-east-1::foundation-model/*` first.

## 3. HTTPS (item 5)

Browsers allow the microphone only on HTTPS (or localhost), so the public site needs a certificate.

| | Caddy on the instance (recommended) | CloudFront in front | ALB with ACM |
|---|---|---|---|
| Extra cost to 29 Jan | $0 (domain optional) | $0 within the always-free allowance of 1 TB and 10 M requests per month (checked on third-party pages, the official page did not load) | At least $0.0225 per hour for the load balancer = $35.64 for 1,584 h, plus LCU charges ([AWS ELB pricing](https://aws.amazon.com/elasticloadbalancing/pricing/)) |
| Domain needed | No: `<ip>.sslip.io` works (third-party DNS), or your own | No: `*.cloudfront.net` default certificate | Yes: ACM cannot issue for the load balancer's own DNS name |
| WebSocket | Yes, no idle timeout | Yes, needs the AllViewer origin request policy or the `Sec-WebSocket-*` headers ([CloudFront WebSocket docs](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/distribution-working-with.websockets.html)) | Yes |
| Leg to the app | HTTPS to the browser, local plain HTTP inside the box | HTTPS to the browser, then plain HTTP from CloudFront to the instance (the access code travels in it) | HTTPS, then HTTP to the instance |
| Gotchas | Needs 443 open to the world; Let's Encrypt must reach the host | Origin response timeout applies between packets (default 30 s, [docs](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesOrigin.html)), so idle WebSockets would drop unless the server sends pings (`voice/server.py` sets no heartbeat). Needs a reverse proxy on the box anyway because the app binds 127.0.0.1. | Idle timeout settings, a second subnet, a target group |
| Complexity | One binary, one file (`deploy/Caddyfile`) | A distribution plus a proxy on the box | Most pieces |

Recommendation: **Caddy**. It costs nothing, has the fewest parts, keeps TLS to the browser without a plaintext
hop through a CDN, and does not time out idle WebSockets. CloudFront stays the fallback if you do not want port 443 open
to the world; its managed prefix list `com.amazonaws.global.cloudfront.origin-facing` restricts the security group to
CloudFront ([AWS blog](https://aws.amazon.com/blogs/networking-and-content-delivery/limit-access-to-your-origins-using-the-aws-managed-prefix-list-for-amazon-cloudfront/)).
That fallback would need a one-line `heartbeat` on the server's WebSocket responses; I did not touch `voice/server.py`.

### How the Origin check interacts with a proxy

`voice/server.py` (`_origin_ok`) refuses an upgrade when the `Origin` header's host differs from the `Host` header
the app receives. A browser on `https://necro.example.com` sends `Origin: https://necro.example.com`, so the proxy
must pass `Host: necro.example.com` through unchanged. Caddy's `reverse_proxy` does this by default; the Caddyfile
warns against `header_up Host ...`. With CloudFront the viewer Host must be forwarded (the AllViewer policy does;
`AllViewerExceptHostHeader` does not, and would give 403). Upgrade headers (`Upgrade`, `Connection`,
`Sec-WebSocket-*`) must be forwarded; Caddy does it automatically. One more effect: the app sees every client as
127.0.0.1, so the per-address wrong-code lockout (5 per minute) becomes the global limit (50 per minute).

### What was tested

- Real Caddy 2.11.9 (amd64, checksum verified) on localhost with its internal CA, in front of the real `voice.server --public`:
  `deploy/smoke_test.py` passed all five checks over HTTPS, including the WebSocket upgrade and the Origin check. The
  `Caddyfile` validates (`caddy validate`).
- `tests/test_deploy_smoke.py` runs the smoke test against a local public-mode app and against a proxy that rewrites
  `Host`; the smoke test reports that failure.
- Not tested: a real Let's Encrypt issuance (needs a public hostname), the CloudFront and ALB options, behaviour on arm64,
  `systemd` units on a real host (`bootstrap.sh` passes `bash -n` and shellcheck; it was not executed).

## 4. Getting the index onto the instance

| | Copy the 973 MiB file (recommended) | Rebuild from the 443 MB CMU archive |
|---|---|---|
| Time | Upload time: about 14 min at 10 Mbit/s, about 3 min at 50 Mbit/s (arithmetic, not measured) | Download + extract 2.6 GB + ingest about 12 min on the dev machine; not measured on a t4g.small, where burstable CPU makes it slower and can run up CPU-credit charges |
| Disk | 1 GB | About 3.6 GB until the archive and maildir are deleted |
| Reproducible from the repo | No (but the checksum proves it is the same file) | Yes |
| Privacy | The file leaves the laptop once, to an encrypted EBS volume | The dataset is downloaded from CMU onto the instance; same data either way |
Use `rsync --partial` and compare `sha256sum`. The privacy note in `README.md` applies: do not put the file in a public bucket.
The Wayback cache (`data/wayback/`, plain JSON and HTML from archive.org) is copied the same way, so no visitor request
depends on archive.org; `scripts/prewarm_wayback.py` is the fallback.

## 5. Cache warm-up

`deploy/warm_index.sh` reads the index once (`cat`) before the service starts (`ExecStartPre`). Measured on the dev
machine: first `find_person` 8.97 s cold and 0.21 s warm, a sequential read 1.9 s. On EBS a cold query could come close to
the 20 s tool timeout, which is why the warm-up is in the unit. `voice/server.py` is unchanged.

## 6. Open points for you

- Whether a hosted URL is required by Devpost (separate research).
- The instance type, the hostname, and when to launch.
- The Bedrock price and the caps (T7.5), and the AWS Budgets alarm (T7.6).
- A server-side WebSocket heartbeat only if you switch to CloudFront.
