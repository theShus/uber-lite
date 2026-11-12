openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
  -subj "/CN=*.dev.shus.world" -addext "subjectAltName=DNS:*.dev.shus.world" \
  -keyout /tmp/dev.key -out /tmp/dev.crt
openssl x509 -in /tmp/dev.crt -noout -subject -ext subjectAltName -enddate
kubectl -n kong create secret tls dev-wildcard-tls --cert /tmp/dev.crt --key /tmp/dev.key
rm /tmp/dev.key
kubectl -n kong get secret dev-wildcard-tls
