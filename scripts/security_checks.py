#!/usr/bin/env python3
"""Lab-specific guardrails that cfn-lint and Checkov do not cover.

Fails (exit 1) when a template:
  * opens any inbound port to 0.0.0.0/0 or ::/0 (so no public SSH or RDP);
  * has an unencrypted EBS root or data volume;
  * lets an instance use IMDSv1;
  * contains UserData, SSM documents, associations or maintenance windows
    (the operating systems must stay stock - administration is done by hand);
  * hardcodes an account ID, AMI ID or AWS access key.

Usage: python3 scripts/security_checks.py [template ...]
"""

from __future__ import annotations

import glob
import re
import sys

import yaml


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that accepts CloudFormation short-form tags (!Ref, !Sub...)."""


def _construct_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {tag_suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag_suffix: loader.construct_sequence(node, deep=True)}
    return {tag_suffix: loader.construct_mapping(node, deep=True)}


_CfnLoader.add_multi_constructor("!", _construct_tag)

OPEN_CIDRS = {"0.0.0.0/0", "::/0"}
FORBIDDEN_TYPES = {
    "AWS::SSM::Document": "SSM documents would automate OS administration",
    "AWS::SSM::Association": "SSM associations would automate OS administration",
    "AWS::SSM::MaintenanceWindow": "maintenance windows would automate patching",
    "AWS::SSM::MaintenanceWindowTask": "maintenance windows would automate patching",
    "AWS::SSM::PatchBaseline": "patching is a manual exercise",
    "AWS::EC2::LaunchTemplate": "launch templates are not used; keep instances in linux/windows-ec2.yaml",
}
HARDCODED = [
    (re.compile(r"\bami-[0-9a-f]{8,17}\b"), "hardcoded AMI ID"),
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "AWS access key ID"),
    (re.compile(r"(?<![0-9a-f])\d{12}(?![0-9a-f])"), "possible hardcoded account ID"),
]


def _ingress_rules(resource: dict) -> list[dict]:
    props = resource.get("Properties") or {}
    if resource.get("Type") == "AWS::EC2::SecurityGroup":
        return list(props.get("SecurityGroupIngress") or [])
    if resource.get("Type") == "AWS::EC2::SecurityGroupIngress":
        return [props]
    return []


def check_template(path: str) -> list[str]:
    problems: list[str] = []
    text = open(path, encoding="utf-8").read()
    for lineno, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        for pattern, label in HARDCODED:
            if pattern.search(code):
                problems.append(f"{path}:{lineno}: {label}: {line.strip()}")

    doc = yaml.load(text, Loader=_CfnLoader) or {}
    for name, res in (doc.get("Resources") or {}).items():
        rtype = res.get("Type", "")
        props = res.get("Properties") or {}
        where = f"{path}: {name}"

        if rtype in FORBIDDEN_TYPES:
            problems.append(f"{where}: {rtype} not allowed ({FORBIDDEN_TYPES[rtype]})")

        for rule in _ingress_rules(res):
            cidr = rule.get("CidrIp") or rule.get("CidrIpv6")
            if isinstance(cidr, str) and cidr in OPEN_CIDRS:
                problems.append(
                    f"{where}: inbound {rule.get('IpProtocol')} "
                    f"{rule.get('FromPort')}-{rule.get('ToPort')} open to {cidr}"
                )

        if rtype == "AWS::EC2::Instance":
            if "UserData" in props:
                problems.append(f"{where}: UserData present - the OS must stay stock")
            tokens = (props.get("MetadataOptions") or {}).get("HttpTokens")
            if tokens != "required":
                problems.append(f"{where}: IMDSv2 not enforced (MetadataOptions.HttpTokens)")
            for bdm in props.get("BlockDeviceMappings") or []:
                ebs = bdm.get("Ebs") or {}
                if ebs and ebs.get("Encrypted") is not True:
                    problems.append(f"{where}: {bdm.get('DeviceName')} is not encrypted")

        if rtype == "AWS::EC2::Volume" and props.get("Encrypted") is not True:
            problems.append(f"{where}: volume is not encrypted")

    return problems


def main(argv: list[str]) -> int:
    paths = argv or sorted(glob.glob("cloudformation/*.yaml") + glob.glob("bootstrap/*.yaml"))
    problems = [p for path in paths for p in check_template(path)]
    for p in problems:
        print(f"FAIL {p}")
    print(f"{len(paths)} template(s) checked, {len(problems)} problem(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
