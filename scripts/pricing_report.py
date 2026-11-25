"""Read EC2/EBS/IPv4/data-transfer list prices from the AWS Pricing API and total them to a date.

READ-ONLY: only `aws pricing get-products --region us-east-1` is called (profile `default`).
It prints every price row it used with its usagetype, so the filters can be checked by eye.

  .venv/bin/python scripts/pricing_report.py [--until 2027-01-29] [--disk-gb 30] [--egress-gb 20]

Strict filters: location "US East (N. Virginia)", operatingSystem Linux, tenancy Shared,
preInstalledSw NA, capacitystatus Used, and the usagetype must equal BoxUsage:<type> exactly.
Prices are On-Demand USD per hour (EC2), per GB-month (gp3) or per hour (public IPv4).
Stops with a clear message if the credentials are missing or expired (run `aws login` yourself).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys

LOCATION = "US East (N. Virginia)"
TYPES = ("t3.small", "t3.medium", "t4g.small")


def pricing(service: str, filters: dict) -> list[dict]:
    cmd = ["aws", "pricing", "get-products", "--region", "us-east-1", "--service-code", service,
           "--output", "json"]
    # one --filters option with every filter after it (a repeated option keeps only the last)
    cmd += ["--filters"] + [f"Type=TERM_MATCH,Field={k},Value={v}" for k, v in filters.items()]
    env = {**os.environ, "AWS_PROFILE": "default"}
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    if r.returncode != 0:
        msg = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "unknown error"
        sys.exit(f"STOP: pricing call failed ({msg}). If credentials expired, run `aws login` yourself.")
    return [json.loads(x) for x in json.loads(r.stdout)["PriceList"]]


def on_demand_rows(items: list[dict]):
    for it in items:
        attrs = dict(it["product"]["attributes"], sku=it["product"]["sku"])
        for term in it["terms"].get("OnDemand", {}).values():
            for dim in term["priceDimensions"].values():
                yield attrs, dim


def rows_for(service, filters, usagetype_exact=None, unit=None):
    out = []
    for attrs, dim in on_demand_rows(pricing(service, filters)):
        ut = attrs.get("usagetype")
        if usagetype_exact and ut != usagetype_exact:
            continue
        if attrs.get("productFamily") == "Compute Instance" and (
                attrs.get("operation") != "RunInstances" or attrs.get("operatingSystem") != "Linux"
                or attrs.get("tenancy") != "Shared" or attrs.get("location") != LOCATION):
            continue
        if unit and dim["unit"] != unit:
            continue
        out.append((attrs, dim, float(dim["pricePerUnit"]["USD"])))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="2027-01-29")
    ap.add_argument("--today", default=None, help="override the run date (YYYY-MM-DD)")
    ap.add_argument("--disk-gb", type=float, default=16.0, help="root volume size (OS about 8 GB, index 1 GB)")
    ap.add_argument("--egress-gb", type=float, default=20.0, help="data out to the internet, whole period")
    args = ap.parse_args()
    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    end = datetime.date.fromisoformat(args.until)
    days = (end - today).days + 1  # both days inclusive, host runs 24 h a day
    hours = days * 24
    print(f"Run date: {today}   until: {end} (inclusive)   days: {days}   hours: {hours}")
    print(f"Assumes launch on the run date, always on, {args.disk_gb:g} GB gp3, {args.egress_gb:g} GB egress.\n")

    base = {"location": LOCATION, "operatingSystem": "Linux", "tenancy": "Shared",
            "preInstalledSw": "NA", "capacitystatus": "Used"}
    hourly: dict[str, float] = {}
    for t in TYPES:
        rows = rows_for("AmazonEC2", {**base, "instanceType": t}, f"BoxUsage:{t}", "Hrs")
        print(f"[EC2 {t}] {len(rows)} row(s)")
        for a, d, p in rows:
            print(f"   usagetype={a['usagetype']} os={a['operatingSystem']} tenancy={a['tenancy']} "
                  f"op={a.get('operation')} loc={a['location']} vcpu={a['vcpu']} mem={a['memory']} arch={a.get('physicalProcessor')} "
                  f"sku={a['sku']} ${p}/{d['unit']}")
        if len(rows) != 1:
            sys.exit(f"STOP: expected exactly one row for {t}, got {len(rows)}")
        hourly[t] = rows[0][2]

    gp3 = rows_for("AmazonEC2", {"location": LOCATION, "productFamily": "Storage", "volumeApiName": "gp3"},
                   "EBS:VolumeUsage.gp3", "GB-Mo")
    print(f"\n[EBS gp3] {len(gp3)} row(s)")
    for a, d, p in gp3:
        print(f"   usagetype={a['usagetype']} volumeApiName={a['volumeApiName']} loc={a['location']} "
              f"sku={a['sku']} ${p}/{d['unit']}")
    if len(gp3) != 1:
        sys.exit("STOP: expected exactly one gp3 row")
    gp3_gb_month = gp3[0][2]

    ipv4 = rows_for("AmazonVPC", {"location": LOCATION, "group": "VPCPublicIPv4Address"},
                    "USE1-PublicIPv4:InUseAddress", "Hrs")
    print(f"\n[Public IPv4 in use] {len(ipv4)} row(s)")
    for a, d, p in ipv4:
        print(f"   usagetype={a['usagetype']} group={a.get('group')} loc={a['location']} "
              f"sku={a['sku']} ${p}/{d['unit']}")
    if len(ipv4) != 1:
        sys.exit("STOP: expected exactly one public IPv4 row")
    ipv4_hour = ipv4[0][2]

    dto = rows_for("AWSDataTransfer", {"fromLocation": LOCATION, "transferType": "AWS Outbound"},
                   "DataTransfer-Out-Bytes", "GB")
    print(f"\n[Data transfer out to internet] {len(dto)} row(s) (tiers)")
    tiers = []
    for a, d, p in sorted(dto, key=lambda r: float(r[1]["beginRange"])):
        print(f"   usagetype={a['usagetype']} from={a['fromLocation']} type={a['transferType']} "
              f"range={d['beginRange']}-{d['endRange']} GB sku={a['sku']} ${p}/{d['unit']}")
        tiers.append((float(d["beginRange"]), p))
    if not tiers:
        sys.exit("STOP: no data transfer rows")
    # List price of the first (0 to 10 TB) tier. The account-level free allowance (100 GB/month
    # across services) is described on the EC2 pricing page and is NOT in the API.
    egress_rate = tiers[0][1]

    months = days / 30.4375
    print(f"\nMonths (days/30.4375): {months:.3f}\n")
    disk = gp3_gb_month * args.disk_gb * months
    ip = ipv4_hour * hours
    egress_list = egress_rate * args.egress_gb
    print(f"gp3 {args.disk_gb:g} GB: ${disk:.2f}   public IPv4: ${ip:.2f}   "
          f"egress {args.egress_gb:g} GB at list ${egress_rate}/GB: ${egress_list:.2f} "
          f"(likely $0 inside the 100 GB/month free allowance)\n")
    print(f"{'option':<12}{'$/hour':>9}{'compute':>10}{'+disk':>8}{'+IPv4':>8}{'total':>9}{'total+egress list':>20}")
    for t in TYPES:
        comp = hourly[t] * hours
        tot = comp + disk + ip
        print(f"{t:<12}{hourly[t]:>9.4f}{comp:>10.2f}{disk:>8.2f}{ip:>8.2f}{tot:>9.2f}{tot + egress_list:>20.2f}")
    print("\nBedrock voice usage is NOT included (price unverified; read it in the Bedrock console).")


if __name__ == "__main__":
    main()
