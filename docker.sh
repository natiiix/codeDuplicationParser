#!/bin/sh

docker build -t cyclone .
docker run -p 5000:5000 -it --rm --name cyclone-running cyclone
