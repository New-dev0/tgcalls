#include "VideoCaptureInterface.h"

#include "VideoCaptureInterfaceImpl.h"

namespace tgcalls {

std::unique_ptr<VideoCaptureInterface> VideoCaptureInterface::Create(
   std::shared_ptr<Threads> threads,
   rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource,
   std::string deviceId,
   bool isScreenCapture,
   std::shared_ptr<PlatformContext> platformContext) {
  return std::make_unique<VideoCaptureInterfaceImpl>(deviceId, isScreenCapture, platformContext, std::move(threads), videoSource);
}

VideoCaptureInterface::~VideoCaptureInterface() = default;

} // namespace tgcalls
