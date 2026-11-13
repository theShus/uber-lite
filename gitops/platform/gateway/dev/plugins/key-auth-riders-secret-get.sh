#this is the command ran to gen a secret, this file is kept only for documentatnion purposes
kubectl -n uber-lite create secret generic partner-app-key \
  --from-literal=key="$(openssl rand -hex 16)" --dry-run=client -o yaml \
  | kubectl label -f - --local konghq.com/credential=key-auth -o yaml \
  | kubectl apply -f -
