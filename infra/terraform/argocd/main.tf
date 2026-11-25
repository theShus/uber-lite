# THIS terraform is a Terra-GitOps bridge, meaning it will bridge data created by terraform into the ArgoCD for it to continue building the platform
# we will load the cluster info, RDS master user and CI docker image location/prefix so argo can build the apps

# network, ci, data and EKS need to be built before argo so we can get the data we need from the outputs
# other terraform components are not important for Argo so they can run before/parallel/after we dont care

data "terraform_remote_state" "network" { # aws VPC id (the Load Balancer Controller needs it, through the root)
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "network/terraform.tfstate", region = var.region }
}

data "terraform_remote_state" "ci" { # location of images
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "ci/terraform.tfstate", region = var.region }
}

data "terraform_remote_state" "data" { # RDS master user
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "data/prod/terraform.tfstate", region = var.region }
}

data "aws_eks_cluster" "hub" { # endpoint + CA for the helm provider (versions.tf), and the cluster name
  name = "ul-prod"
}

locals {
  # we load master secret arn from data output. WE NEED TRY() because make down destroys this layer and the data layer
  # at the same time (a RACE): once data is destroyed its state has no outputs, and reading a missing output is an error,
  # so the destroy of THIS layer would fail. try() gives "" instead; the precondition below still blocks an apply with ""

  db_master_secret_arn = try(data.terraform_remote_state.data.outputs.master_secret_arn, "")

  # clusters is the values-prod.yaml for argo helm install (gitops\bootstrap\argocd\values-dev.yaml)
  # why is it in terraform and not yaml? because it holds my aws account id / registry that we dont want to hard code and put on git

  clusters = {
    in-cluster = {
      server      = "https://kubernetes.default.svc"
      labels      = { "uber-lite/env" = "prod", "uber-lite/services" = "true" }
      annotations = { "uber-lite/image-prefix" = "${data.terraform_remote_state.ci.outputs.registry}/uber-lite/" }
      config      = { tlsClientConfig = { insecure = false } }
    }
  }

  # The root Application - same as the root (app of apps) we had for dev (gitops\bootstrap\dev\root.yaml)
  # why not yaml - because we want a one command cluster build, this way we can run the app as we run the terra argocd chart
  # else we would need to with a command / manually go to EKS and run the k apply -f root.yaml
  # + it has vpc id and other stuff in it that we want to hide

  roots = {
    root-prod = {
      namespace = "argocd"
      project   = "default"
      source = {
        repoURL        = var.repo_url
        targetRevision = "main"
        path           = "gitops/bootstrap/root"
        helm = {
          valuesObject = {
            env = "prod"
            cluster = {
              name              = data.aws_eks_cluster.hub.name
              region            = var.region
              vpcId             = data.terraform_remote_state.network.outputs.vpc_id
              dbMasterSecretArn = local.db_master_secret_arn
            }
          }
        }
      }
      destination = { server = "https://kubernetes.default.svc", namespace = "argocd" }
      syncPolicy  = { automated = { prune = true, selfHeal = true } }
    }
  }
}

# this is the helm chart install that installs ArgoCD (controller pods and CRDs) and we give it values.yaml and local.clusters (values-prod.yaml) files
resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = var.argocd_chart_version
  namespace        = "argocd"
  create_namespace = true
  wait             = true
  timeout          = 600

  # The shared values file from your checkout, then this cluster's Secret. Helm merges the maps.
  values = [
    file("${path.module}/../../../gitops/bootstrap/argocd/values.yaml"),
    yamlencode({ configs = { clusterCredentials = local.clusters } }),
  ]
}

# THIS IS THE ROOT APP RUN
# this is the equivalent of typing "k apply -f root.yaml" manually.
# why does it exist? we cant simply use kubernetes_manifest (a resource of the terraform kubernetes provider, it talks to
# the API server, no kubectl involved): it needs the CRD's schema already at PLAN time, and before argo is installed
# there is no Application CRD, so the plan fails. no reason to add additional providers and others
# so Argo chart made this wrapper that you can input your root app yaml and it will start the root app

resource "helm_release" "bootstrap" {
  name       = "bootstrap"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = "2.0.6"
  namespace  = "argocd"

  depends_on = [helm_release.argocd] # the Application CRD has to exist first

  values = [yamlencode({ applications = local.roots })] # <-- giving it the root app yaml here

  lifecycle {
    precondition {
      condition     = startswith(local.db_master_secret_arn, "arn:aws:secretsmanager:")
      error_message = "The data layer has no master secret for prod. Run 'make data-up ENV=prod' first."
    }
  }
}
