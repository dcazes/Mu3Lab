# MiniCPM-o 4.5's full-duplex server (llama-omni-server from llama.cpp-omni), built for NVIDIA GPUs.
# The source is pinned to one commit and carries one Mu3Lab patch (duplex-text.patch): full-duplex
# input may carry text after each second of audio, which is how a search result reaches the model.
FROM nvidia/cuda:12.8.1-devel-ubuntu24.04@sha256:520292dbb4f755fd360766059e62956e9379485d9e073bbd2f6e3c20c270ed66 AS build

ARG OMNI_COMMIT=a64932ec615e1463158129feaf2f0752d0c07a2e
# Turing, Ampere (data centre and consumer), Ada and Blackwell consumer cards.
ARG CUDA_ARCHITECTURES="75;80;86;89;120"

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git init -q llama.cpp-omni \
    && git -C llama.cpp-omni fetch -q --depth 1 https://github.com/tc-mb/llama.cpp-omni.git "$OMNI_COMMIT" \
    && git -C llama.cpp-omni checkout -q FETCH_HEAD
WORKDIR /src/llama.cpp-omni
# The CUDA kernels take most of the build and do not change with the patch: built first, in their
# own cached layer.
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
        -DGGML_CUDA=ON -DGGML_NATIVE=OFF -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCHITECTURES" \
        -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
    && cmake --build build --target ggml -j"$(nproc)"
# The patch also guards a macOS-only call (F_NOCACHE) that breaks the Linux build at this commit.
COPY duplex-text.patch /src/
RUN git apply --whitespace=nowarn /src/duplex-text.patch \
    && cmake --build build --target llama-omni-server -j"$(nproc)"

FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04@sha256:ebef3c171eeef0298e4eb2e4be843105edf3b8b0ac45e0b43acee358e8046867

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# The server reads its default voice from tools/omni/assets, relative to where it runs.
WORKDIR /app
COPY --from=build /src/llama.cpp-omni/build/bin/llama-omni-server /usr/local/bin/
COPY --from=build /src/llama.cpp-omni/tools/omni/assets /app/tools/omni/assets
USER 1000:1000
ENTRYPOINT ["llama-omni-server"]
