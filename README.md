# Microservices Demo for Arm and Axion

This repository hosts the Arm-maintained multi-architecture baseline of the Google Cloud `microservices-demo` application used for Arm server learning content and Axion workshops.

It is based on the upstream [GoogleCloudPlatform/microservices-demo](https://github.com/GoogleCloudPlatform/microservices-demo) project, with focused changes to make the storefront services easier to build and run as multi-architecture container images for:

- `linux/amd64`
- `linux/arm64`

## What this repository is for

Use this repository when you want to:

- build multi-architecture container images for the Online Boutique storefront
- validate the same application on x86 and Arm Kubernetes node pools
- support the Arm Axion workshop flow on GKE
- follow or extend the Arm learning path for mixed-architecture GKE deployments

This repository is intended to provide an official Arm-owned source location for the workshop and related learning content, instead of relying on a personal GitHub repository.

## What changed from upstream

The branch in this repository keeps the upstream application structure, but updates a subset of service Dockerfiles to improve multi-architecture build behavior.

The main changes are in service container build definitions, including:

- `src/adservice/Dockerfile`
- `src/cartservice/src/Dockerfile`
- `src/currencyservice/Dockerfile`
- `src/emailservice/Dockerfile`
- `src/loadgenerator/Dockerfile`
- `src/paymentservice/Dockerfile`
- `src/recommendationservice/Dockerfile`

These updates are focused on build portability and architecture-aware image creation. They are not meant to change the user-facing behavior of the application.

## Services in the storefront baseline

The storefront baseline consists of these core services:

- `adservice`
- `cartservice`
- `checkoutservice`
- `currencyservice`
- `emailservice`
- `frontend`
- `paymentservice`
- `productcatalogservice`
- `recommendationservice`
- `shippingservice`
- `redis-cart`

`loadgenerator` is optional for workshop startup and is not required for the core storefront baseline.

## Intended workshop model

For the Axion workshop, this repository is used as the public source tree that participants can pull into Cloud Shell.

The workshop infrastructure itself is expected to provision:

- the Kubernetes cluster
- Arm-based node pools such as N4A and C4A
- Artifact Registry
- the storefront baseline deployment

Participants then work from this repository to:

- inspect the application manifests and source tree
- build and push workshop-specific images where needed
- add the shopping assistant service
- compare workload placement across Arm-based node pools

## Relationship to the Arm learning path

This repository aligns with the Arm learning path for mixed-architecture GKE deployments:

- [Overview](https://learn.arm.com/learning-paths/servers-and-cloud-computing/gke-multi-arch-axion/)
- [Background](https://learn.arm.com/learning-paths/servers-and-cloud-computing/gke-multi-arch-axion/background/)
- [Project setup](https://learn.arm.com/learning-paths/servers-and-cloud-computing/gke-multi-arch-axion/project-setup/)
- [Build multi-architecture images](https://learn.arm.com/learning-paths/servers-and-cloud-computing/gke-multi-arch-axion/multi-arch-images/)
- [Build and push to GKE](https://learn.arm.com/learning-paths/servers-and-cloud-computing/gke-multi-arch-axion/gke-build-push/)
- [Deploy on GKE](https://learn.arm.com/learning-paths/servers-and-cloud-computing/gke-multi-arch-axion/gke-deploy/)

If you want the full step-by-step migration and validation workflow, the learning path is the best starting point.

## Quick usage

Clone this repository and check out the branch used for the Arm multi-architecture baseline:

```bash
git clone https://github.com/ArmDeveloperEcosystem/microservices-demo.git
cd microservices-demo
git checkout arm-multiarch-baseline
```

From there, you can:

1. build multi-architecture images from the service source directories
2. publish them to your container registry
3. update deployment manifests or Kustomize overlays to point at your image registry
4. deploy to amd64 or arm64 node pools and validate placement

## Upstream project

The original application and its broader documentation come from:

- [GoogleCloudPlatform/microservices-demo](https://github.com/GoogleCloudPlatform/microservices-demo)

This Arm repository is not intended to replace the upstream project. It provides an Arm-focused, workshop-friendly baseline derived from it.
