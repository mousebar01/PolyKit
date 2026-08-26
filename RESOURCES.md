# Headless PolyKit Resources

## Knowledge

- [FastAPI: Deployment Concepts](https://fastapi.tiangolo.com/deployment/concepts/)
  Primary reference for startup supervision, restarts, process count, memory duplication, and HTTPS termination.
- [FastAPI in Containers](https://fastapi.tiangolo.com/deployment/docker/)
  Use when packaging the service for a repeatable single-server deployment.
- [PyTorch: `torch.cuda.is_available`](https://docs.pytorch.org/docs/stable/generated/torch.cuda.is_available.html)
  Primary reference for the runtime CUDA availability signal used by the PolyKit headless server.
- [NVIDIA Container Toolkit installation](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  Primary reference for passing an NVIDIA GPU into a production container.

## Wisdom (Communities)

- [FastAPI GitHub Discussions](https://github.com/fastapi/fastapi/discussions)
  Useful for deployment and process-model questions that are not answered by the official guide.
- [NVIDIA Container Toolkit GitHub Issues](https://github.com/NVIDIA/nvidia-container-toolkit/issues)
  Useful for host-driver and container GPU passthrough failures on a concrete server.

## Gaps

- A real-GPU compatibility matrix for the selected PolyKit model adapter will be created after the first NVIDIA test host is available.
