"""Nightly cost guard: list what still bills, and email it through SNS.

EventBridge Scheduler invokes it at 23:00 Europe/Belgrade. It stays quiet when
nothing bills, except on Sundays (input {"heartbeat": true}): then it always
reports, so a silent week means the guard itself is broken.
"""

import datetime
import os

import boto3

REGIONS = os.environ.get("REGIONS", "eu-central-1").split(",")
TOPIC_ARN = os.environ["TOPIC_ARN"]  # "print" = print instead of publishing (local runs)

RUNNING = ["pending", "running", "stopping", "stopped"]


def checks(region):
    """name -> function returning a list of short descriptions of what bills."""
    ec2 = boto3.client("ec2", region_name=region)
    eks = boto3.client("eks", region_name=region)
    elbv2 = boto3.client("elbv2", region_name=region)
    elb = boto3.client("elb", region_name=region)
    rds = boto3.client("rds", region_name=region)

    def instances():
        found = []
        pages = ec2.get_paginator("describe_instances").paginate(
            Filters=[{"Name": "instance-state-name", "Values": RUNNING}]
        )
        for page in pages:
            for reservation in page["Reservations"]:
                for i in reservation["Instances"]:
                    tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
                    found.append(f'{i["InstanceId"]} {i["InstanceType"]} {i["State"]["Name"]} {tags.get("Name", "-")}')
        return found

    return {
        "EKS clusters": lambda: eks.list_clusters()["clusters"],
        "EC2 instances": instances,
        "NAT gateways": lambda: [
            n["NatGatewayId"]
            for n in ec2.describe_nat_gateways(Filters=[{"Name": "state", "Values": ["pending", "available"]}])["NatGateways"]
        ],
        "Load balancers": lambda: [lb["LoadBalancerName"] for lb in elbv2.describe_load_balancers()["LoadBalancers"]],
        "Classic load balancers": lambda: [
            lb["LoadBalancerName"] for lb in elb.describe_load_balancers()["LoadBalancerDescriptions"]
        ],
        "Elastic IPs": lambda: [a["PublicIp"] for a in ec2.describe_addresses()["Addresses"]],
        "EBS volumes": lambda: [f'{v["VolumeId"]} {v["Size"]}GiB {v["State"]}' for v in ec2.describe_volumes()["Volumes"]],
        "RDS instances": lambda: [d["DBInstanceIdentifier"] for d in rds.describe_db_instances()["DBInstances"]],
    }


def lambda_handler(event, context):
    found, failed = [], []
    for region in REGIONS:
        for name, check in checks(region).items():
            try:
                items = check()
            except Exception as e:  # a guard that can't look must say so, not stay quiet
                failed.append(f"? {region} {name}: check failed: {e}")
                continue
            found += [f"✗ {region} {name}: {item}" for item in items]

    heartbeat = bool((event or {}).get("heartbeat"))
    if not found and not failed and not heartbeat:
        print("nothing billing; no email sent")
        return {"found": 0, "failed": 0, "sent": False}

    if found or failed:
        subject = f"[uber-lite guard] {len(found)} billing, {len(failed)} failed checks"
        lines = found + failed
    else:
        subject = "[uber-lite guard] all clear (weekly heartbeat)"
        lines = ["✅ Nothing billing. This weekly email proves the guard still runs."]
    now = datetime.datetime.now(datetime.timezone.utc)
    message = "\n".join(lines) + f"\n\nChecked {', '.join(REGIONS)} at {now:%Y-%m-%d %H:%M} UTC."

    if TOPIC_ARN == "print":
        print(subject, message, sep="\n")
    else:
        boto3.client("sns").publish(TopicArn=TOPIC_ARN, Subject=subject, Message=message)
    print("\n".join(lines))
    return {"found": len(found), "failed": len(failed), "sent": True}


if __name__ == "__main__":
    # Local run on the hub: TOPIC_ARN=print python3 handler.py [heartbeat]
    import sys

    print(lambda_handler({"heartbeat": "heartbeat" in sys.argv}, None))
