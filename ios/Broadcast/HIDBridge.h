#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/// Thin wrapper over the private IOHIDEvent digitizer API (resolved with dlsym at runtime).
/// Requires the HID entitlements in Broadcast.entitlements, which only a TrollStore install provides.
@interface HIDBridge : NSObject
/// YES if all private symbols were found and an event system client was created.
+ (BOOL)isAvailable;
/// touches: array of @{@"id": NSNumber, @"x": NSNumber, @"y": NSNumber, @"down": NSNumber(BOOL)}
/// x,y normalised 0..1 in portrait-up screen space. isMove: only positions changed.
+ (void)sendTouches:(NSArray<NSDictionary *> *)touches isMove:(BOOL)isMove;
@end

NS_ASSUME_NONNULL_END
