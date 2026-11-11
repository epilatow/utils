#!/bin/sh
# A background command writes to the job log when the guard stops it.

if [ "$1" = "--child" ]; then
    cleanup() {
        echo "child cleanup output"
        exit 0
    }
    trap cleanup TERM
    : > "$2"
    while :; do
        sleep 1
    done
fi

/bin/sh "$0" --child "$1" &
while [ ! -e "$1" ]; do
    sleep 0.05
done
if [ "$2" = "signal" ]; then
    kill -TERM "$$"
fi
exit "$2"
