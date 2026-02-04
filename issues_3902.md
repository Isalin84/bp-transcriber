Update on TorchAudio’s future #3902
Open
Open
Update on TorchAudio’s future
#3902
@scotts
Description
scotts
opened on Apr 24, 2025 · edited by NicolasHug
Contributor
EDIT from 2026/01/22: we just released TorchAudio 2.10, which marks the completion of the migration detailed below. Based on user feedback, one important modification was made to the original plan: lfilter, RNNTLoss, CUCT, forced_align, and overdrive, which were originally slated for deprecation and deletion, have been preserved. See #3902 (comment).

Dear TorchAudio users,

TorchAudio is the most popular audio library for PyTorch. It has critical transforms, models and datasets that we know the community relies on. That is why we wanted to let the community know that we have started a refactoring effort to transition TorchAudio into a maintenance phase. This process will involve removal of some user-facing features. We have three goals we want to achieve with this effort:

Make TorchAudio easier to maintain to ensure long-term reliability. We plan to eliminate all C++ code so that TorchAudio is a Python-only library. We also plan to reduce external dependencies as much as possible. Both efforts will simplify testing and release.
Reduce redundancies with the rest of the PyTorch ecosystem. Some of the functionality in TorchAudio is also available in TorchVision and TorchCodec. We are working across all three libraries to ensure a given capability lives in one library.
Focus on TorchAudio’s strengths. Those strengths are the audio transforms, models and datasets that are integral to users training and inference pipelines. As a result, we will deprecate and eventually remove some functionality that is outside of these strengths.
The diagram below depicts the various components of TorchAudio. We have highlighted it according to the user-facing API changes that we are making:

Image
Starting with TorchAudio 2.8 (expected around August 2025), APIs slated for removal will trigger a deprecation warning. These APIs will be fully removed in TorchAudio 2.9 (anticipated by the end of 2025).

Most of the APIs in transforms, functional, compliance.kaldi, models and pipelines modules will remain. These are the APIs that we identified as the most popular and valuable ones.

A few APIs, specifically those relying on C++ implementations like RNNT loss and forced-alignment, may be dropped. Some, like lfilter and overdrive, will switch to pure-Python implementations, which might affect performance. We are exploring options to retain C++-backed APIs, but this is unlikely.
Remaining APIs will be compatible with the latest stable PyTorch version. No new features will be added.
The decoding and encoding capabilities of TorchAudio for both audio and video data will migrate to TorchCodec, where we are consolidating all of PyTorch media decoding and encoding. TorchAudio’s decoding and encoding APIs will be deprecated from TorchAudio 2.8, and they will be removed in TorchAudio 2.9, so we encourage users to migrate to TorchCodec as soon as possible. TorchCodec already supports video and audio decoding, and encoding will be supported soon. While there isn't a direct 1:1 API mapping, the migration process should be smooth. Please report any issues in the TorchCodec repository.

All other modules and APIs will be removed in TorchAudio 2.9.

We understand that these changes may be disruptive. We believe that they are unfortunately necessary, in order for us to guarantee TorchAudio’s stability in the future.

[EDIT from @NicolasHug] We'll also be removing torchaudio from the official installation instructions, starting from the 2.8 release.

Activity

NicolasHug
pinned this issue on Apr 24, 2025
yoyolicoris
yoyolicoris commented on Apr 24, 2025
yoyolicoris
on Apr 24, 2025 · edited by yoyolicoris
Contributor
Hi @scotts, thanks for reporting the status of torchaudio and future plans.

I don't understand the decision to drop the C++/CUDA extensions...
They are implemented because of the super inefficiency if they're done in pure Python (with JIT compilation).
Just like you said, Torchaudio's strength is its various audio transforms.
Thus, they should be kept instead of removed.
Switching back to pure-Python implementations is like going backwards and makes no sense.
These low-level implementations enable a state-of-the-art training speed compared to other libraries. (check out torchaudio 2.1 ASRU paper.)
The lfilter has recently been used in torchfx as the low-level operator for differentiable and fast filtering on GPU.
They're valuable to the community, and the decision to drop them is unwise, disruptive, and disastrous.

There should be more discussions on this before making the decision.
I suggest holding back this decision.

Best wishes,

Chin-Yun
PhD student

Centre for Digital Music
School of Electronic Engineering and Computer Science
Queen Mary University of London
Email: chin-yun.yu@qmul.ac.uk

christhetree
christhetree commented on Apr 25, 2025
christhetree
on Apr 25, 2025
@scotts thanks for the update!
Removing the C++/CUDA extensions is a big step backwards for the community and makes some of the implementations essentially useless due to their slow Python-only versions. I understand some concessions must be made if PyTorch Audio is no longer going to be actively developed, but I would also highly encourage reconsidering the removal of the C++ extensions, at least for the most popular operators.
Thanks!


bruAristimunha
mentioned this on Apr 26, 2025
Torch audio will be refactor braindecode/braindecode#738
bruAristimunha
bruAristimunha commented on Apr 26, 2025
bruAristimunha
on Apr 26, 2025
About lfilter, it would be nice to match the scipy precision and behaviour. I understand in big pictures but a lot of work because of this.

scotts
scotts commented on Apr 26, 2025
scotts
on Apr 26, 2025
Contributor
Author
@yoyolicoris, @christhetree, thanks for taking the time to reply. I understand that removing C++ implementations may be a performance regression for those components. I would like to further explain the motivation for why removing this C++ code specifically improves the long-term health of TorchAudio:

C++ compilation complicates testing. Because we need to use different C++ compilers in the cross product of all supported platforms (Linux, Windows and Mac), architectures (x86, arch64) there's much more chance of breakages. A Python only repo reduces the testing matrix down to just platform and Python version.
C++ binaries complicates release. Each entry in the cross product of platforms, architectures, device and Python version requires a separate wheel. Because of this, we can see that the "TorchAudio 2.7 release" is actually 109 wheel files. A Python only repo reduces that down to the same number of wheels as supported devices, which I think would be just 4.
The Torch C++ API is not ABI-stable, and all libraries that use the C++ API must release with each new version of PyTorch. This means that point 1 and 2 must be dealt with on the regular PyTorch release cadence which is roughly every 3 months.
In the update, we did say: "We are exploring options to retain C++-backed APIs, but this is unlikely." Specifically, that exploration is if we can take advantage of a new effort in PyTorch 2.7, which is a stable ABI. That only addresses point 3, but addressing point 3 could greatly reduce the cost of point 2. The cost of point 1 would still stand, though. For those interested in retaining various C++ components, let us know if you have the capacity to explore porting these components to the stable ABI. That changes the maintenance cost equation.

vadimkantorov
vadimkantorov commented on Apr 28, 2025
vadimkantorov
on Apr 28, 2025 · edited by vadimkantorov
Make TorchAudio easier to maintain to ensure long-term reliability. We plan to eliminate all C++ code so that TorchAudio is a Python-only library. We also plan to reduce external dependencies as much as possible. Both efforts will simplify testing and release.
Maybe for some other C++ components, the model could be to factor them out in separate repo which doesn't provide binaries releases and supports only some GitHub Actions CI for testing and relies on users who must build it themselves

Also, for some C++ code, maybe load_inline(...) method can be used / improved: https://pytorch.org/docs/stable/cpp_extension.html#torch.utils.cpp_extension.load_inline for simplifying build scripts. Like so - the user would be responsible for having the working toolchain, and binaries would be built on the enduser's machine

Also, maybe a way forward would be to convert some C++ code to pure C API (e.g. could work for ffmpeg effects), to be called via ctypes (and use DLPack API or pure pointers for passing tensors for processing). This should eliminate the problem of unstable PyTorch C++ ABI.

Regarding ffmpeg effects, maybe they could also be moved to torchcodec, as working with ffmpeg filter chains would be a very useful feature...

Another useful component in torchaudio are bindings to flashlight, but flashlight itself is discontinued for several years now. So probably the best path there would be factoring out flashlight C++ code + python bindings in torchaudio in a new standalone repo like Nvidia did: https://github.com/nvidia-riva/riva-asrlib-decoder . This is already half-done into https://github.com/flashlight/text, but would be nice to maybe move the Python bindings https://pytorch.org/audio/0.12.0/models.decoder.html next to it? Also, given that Flashlight itself is discontinued, maybe worth moving the decoder out of the Flashlight org? to the pytorch org?


vadimkantorov
mentioned this in 2 issues on Apr 28, 2025
[discussion] Consolidation of audio-visual I/O in a new package pytorch#81102
why CTCDecodingConfig dont work? NVIDIA-NeMo/NeMo#13155
parsasabetz
parsasabetz commented on May 8, 2025
parsasabetz
on May 8, 2025
Thank you for sharing this.
I respect and love what you guys are doing, but you're treating Python like it's not Python.
You already know that this means most of the library's APIs are going to be tens of times (if not hundreds of times) slower and more inefficient by all measures... Dropping C++ is not worth it here, it's not possible to match the performance with Python. To be fair, it's fast because it's not really Python code.

Thanks for all the efforts,
I hope you refine your plans for TorchAudio at least to some extent.

vadimkantorov
vadimkantorov commented on May 12, 2025
vadimkantorov
on May 12, 2025 · edited by vadimkantorov
@scotts Also, might be interesting to promote some of stable signal processing functions / modules into PyTorch core (e.g. new torch.signal namespace akin to https://docs.scipy.org/doc/scipy/reference/signal.html)?

vadimkantorov
vadimkantorov commented on May 16, 2025
vadimkantorov
on May 16, 2025 · edited by vadimkantorov
Another solution might be:

moving all python-only models/code to HuggingFace
moving mature functions/transforms to core pytorch
moving all other C++ extensions to use torch.utils.cpp_extensions.load_inline or nvrtc (via https://github.com/NVIDIA/cuda-python)
maybe when possible - let go of using libtorch / torch::Tensor interface and replace it with DLPack interfacing or raw pointers, this would make user-side compilation very stable

vadimkantorov
mentioned this on May 16, 2025
RFC: The State of Custom CUDA extensions in PyTorch pytorch#152032

samanklesaria
mentioned this on Jun 9, 2025
Add deprecation warnings #3924

NicolasHug
mentioned this on Jun 13, 2025
Add installation script for windows arm64 dependencies #3916
66 remaining items

ShaaranSr
mentioned this on Dec 10, 2025
_pickle.UnpicklingError: Weights only load failed. m-bain/whisperX#1304
pearu
pearu commented on Dec 13, 2025
pearu
on Dec 13, 2025
Collaborator
Starting from torchaudio 2.10, all its extension libraries use LibTorch Stable ABI.

For torchaudio 2.11, we aim at porting torchaudio to use Python Stable ABI.


lubosz
mentioned this on Dec 14, 2025
df/io: Add support for torchaudio 2.9. Rikorose/DeepFilterNet#670

lubosz
added a commit that references this issue on Dec 14, 2025
df/io: Add support for torchaudio 2.9.

Verified
5a3bfe8

idjuricTT
mentioned this on Dec 23, 2025
Replace torch with torchcodec for np array generation tenstorrent/tt-inference-server#1547

wortspieler
mentioned this on Dec 29, 2025
Fehlermeldung: code 3221226505 kaixxx/noScribe#259
YuanJie2001
YuanJie2001 commented last month
YuanJie2001
last month
I'm using version 2.9.0 of torch_audio. I need to use forced_align. Is there an alternative to this function?


Puiching-Memory
mentioned this last month
Crash on startup: AttributeError: torchaudio.list_audio_backends (TorchAudio 2.9 removal) fishaudio/fish-speech#1118

submagr
mentioned this last month
Is torchaudio 2.9 supported? pyannote/pyannote-audio#1974
vadimkantorov
vadimkantorov commented 2 weeks ago
vadimkantorov
2 weeks ago · Hidden as duplicate

Safari77
added a commit that references this issue 2 weeks ago
Add support for torchaudio 2.9

a9a8483
NicolasHug
NicolasHug commented 2 weeks ago
NicolasHug
2 weeks ago · edited by NicolasHug
Member
Hi everyone,

We just released TorchAudio 2.10, which marks the completion of the migration. It is compatible with torch 2.10 and should be compatible with future torch versions as well.

Good news: based on your feedback, the C++ and CUDA extensions we'd marked for deprecation are staying (lfilter, RNNTLoss, CUCT, forced_align, and overdrive). Huge thanks to @pearu for the fantastic effort of porting these to the new 'torch stable ABI' API!

We realize this migration was disruptive, and we appreciate everyone's patience and constructive feedback during the process. This was a tough but necessary step to keep TorchAudio sustainable long-term.

yoyolicoris
yoyolicoris commented 2 weeks ago
yoyolicoris
2 weeks ago
Contributor
Hi @NicolasHug,
This is fantastic news! The migration is not easy.
Thank you to all the devs for the time and effort they put into preserving these functions.

