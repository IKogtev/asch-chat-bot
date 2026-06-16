#!/bin/bash

tag=$1

if [ -n "$tag" ]
then
    target_tag=$tag
else
    target_tag=latest
fi

docker build --no-cache --network host -t cr.yandex/crp4m4ne3bkrq9l2ffbi/aszh-chatbot/adk-agent-tester:$target_tag .
docker push cr.yandex/crp4m4ne3bkrq9l2ffbi/aszh-chatbot/adk-agent-tester:$target_tag
