#!/bin/bash

tag=$1

if [ ! -z $tag ]
then
    docker build --no-cache . -t cr.yandex/crp4m4ne3bkrq9l2ffbi/aszh-chatbot/adk-agent-tester:$tag
    docker push cr.yandex/crp4m4ne3bkrq9l2ffbi/aszh-chatbot/adk-agent-tester:$tag
else
    docker build --no-cache . -t cr.yandex/crp4m4ne3bkrq9l2ffbi/aszh-chatbot/adk-agent-tester:latest
    docker push cr.yandex/crp4m4ne3bkrq9l2ffbi/aszh-chatbot/adk-agent-tester:latest
fi
