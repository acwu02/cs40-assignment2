#!/bin/bash

set -euxo pipefail

echo "RUN THIS SCRIPT FROM YOUR assignment2 ROOT DIRECTORY. MAKE SURE YOU HAVE SOURCED YOUR venv"

pip3 install pyyaml jinja2

pushd cdk
cdk synth
popd

git clone https://github.com/infracourse/assignment4-autograder autograder

opa eval -b autograder/rules/ -i <(jq -s 'reduce .[] as $item ({}; .Resources += $item.Resources) | del(.Resources.CDKMetadata)' cdk/cdk.out/yoctogram-network-stack.template.json cdk/cdk.out/yoctogram-data-stack.template.json cdk/cdk.out/yoctogram-compute-stack.template.json) -f json 'data.rules.main' | jq -r .result[].expressions[].value.violations[]

python3 autograder/grade_action.py $1

rm -rf autograder
