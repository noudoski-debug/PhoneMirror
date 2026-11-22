#import "HIDBridge.h"
#import <dlfcn.h>
#import <mach/mach_time.h>

typedef void *IOHIDEventRef;
typedef void *IOHIDEventSystemClientRef;

typedef IOHIDEventSystemClientRef (*ClientCreateFn)(CFAllocatorRef);
typedef void (*ClientDispatchFn)(IOHIDEventSystemClientRef, IOHIDEventRef);
// IOHIDFloat is double on 64-bit (matches the private IOKit headers).
typedef double IOHIDFloat;
typedef IOHIDEventRef (*CreateDigitizerEventFn)(CFAllocatorRef, uint64_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t,
                                                IOHIDFloat, IOHIDFloat, IOHIDFloat, IOHIDFloat, IOHIDFloat, Boolean, Boolean, uint32_t);
typedef IOHIDEventRef (*CreateFingerEventFn)(CFAllocatorRef, uint64_t, uint32_t, uint32_t, uint32_t,
                                             IOHIDFloat, IOHIDFloat, IOHIDFloat, IOHIDFloat, IOHIDFloat, Boolean, Boolean, uint32_t);
typedef void (*AppendEventFn)(IOHIDEventRef, IOHIDEventRef, uint32_t);
typedef void (*SetIntFn)(IOHIDEventRef, uint32_t, int);
typedef void (*SetSenderFn)(IOHIDEventRef, uint64_t);

static ClientCreateFn pClientCreate;
static ClientDispatchFn pDispatch;
static CreateDigitizerEventFn pCreateDigitizer;
static CreateFingerEventFn pCreateFinger;
static AppendEventFn pAppend;
static SetIntFn pSetInt;
static SetSenderFn pSetSender;
static IOHIDEventSystemClientRef gClient;
static BOOL gAvailable;

// IOHIDEventTypes.h values
static const uint32_t kTransducerHand = 3;
static const uint32_t kEventRange = 1u << 0;
static const uint32_t kEventTouch = 1u << 1;
static const uint32_t kEventPosition = 1u << 2;
static const uint32_t kFieldIsDisplayIntegrated = 0xB0019;
static const uint64_t kSenderID = 0xDEFACEDBEEFFECE5ULL;

@implementation HIDBridge

+ (void)load {
    void *iokit = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_NOW);
    if (!iokit) return;
    pClientCreate = dlsym(iokit, "IOHIDEventSystemClientCreate");
    pDispatch = dlsym(iokit, "IOHIDEventSystemClientDispatchEvent");
    pCreateDigitizer = dlsym(iokit, "IOHIDEventCreateDigitizerEvent");
    pCreateFinger = dlsym(iokit, "IOHIDEventCreateDigitizerFingerEvent");
    pAppend = dlsym(iokit, "IOHIDEventAppendEvent");
    pSetInt = dlsym(iokit, "IOHIDEventSetIntegerValue");
    pSetSender = dlsym(iokit, "IOHIDEventSetSenderID");
    if (!(pClientCreate && pDispatch && pCreateDigitizer && pCreateFinger && pAppend && pSetInt && pSetSender)) return;
    gClient = pClientCreate(kCFAllocatorDefault);
    gAvailable = (gClient != NULL);
}

+ (BOOL)isAvailable { return gAvailable; }

+ (void)sendTouches:(NSArray<NSDictionary *> *)touches isMove:(BOOL)isMove {
    if (!gAvailable || touches.count == 0) return;
    uint64_t now = mach_absolute_time();
    BOOL anyDown = NO;
    for (NSDictionary *t in touches) anyDown |= [t[@"down"] boolValue];
    uint32_t mask = isMove ? kEventPosition : (kEventRange | kEventTouch);

    IOHIDEventRef parent = pCreateDigitizer(kCFAllocatorDefault, now, kTransducerHand, 0, 0, mask, 0,
                                            0, 0, 0, 0, 0, anyDown, anyDown, 0);
    if (!parent) return;
    pSetInt(parent, kFieldIsDisplayIntegrated, 1);
    for (NSDictionary *t in touches) {
        BOOL down = [t[@"down"] boolValue];
        float x = fminf(1.f, fmaxf(0.f, [t[@"x"] floatValue]));
        float y = fminf(1.f, fmaxf(0.f, [t[@"y"] floatValue]));
        uint32_t ident = [t[@"id"] unsignedIntValue] + 2;
        IOHIDEventRef finger = pCreateFinger(kCFAllocatorDefault, now, ident, ident, mask,
                                             (IOHIDFloat)x, (IOHIDFloat)y, 0, down ? 1.0 : 0.0, 0, down, down, 0);
        if (!finger) continue;
        pSetInt(finger, kFieldIsDisplayIntegrated, 1);
        pAppend(parent, finger, 0);
        CFRelease(finger);
    }
    pSetSender(parent, kSenderID);
    pDispatch(gClient, parent);
    CFRelease(parent);
}

@end
