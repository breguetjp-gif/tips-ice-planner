//
//  PluginFilter.h  — Miele-LXIV / OsiriX API の最小宣言（逆算）
//  MieleAPI.framework の Headers が空のため、実機バイナリ(nm/otool)と
//  spalte/OsiriXAPI のヘッダから必要分だけ復元。実体は本体exe miele-lxiv が
//  提供し、-bundle_loader でリンク時に解決、ロード時に dyld が結線する。
//

#import <Cocoa/Cocoa.h>

@class ViewerController;
@class DCMView;

// ---- 1枚のCT画像（float画素・幾何） ----
@interface DCMPix : NSObject
- (float*) fImage;          // float画素バッファ(CTはHU)。長さ = pwidth*pheight
- (long) pwidth;
- (long) pheight;
- (double) pixelSpacingX;
- (double) pixelSpacingY;
- (double) sliceLocation;   // mm
- (double) sliceThickness;
- (double) sliceInterval;
@end

// ---- 2Dビューア（表示中シリーズ） ----
@interface ViewerController : NSObject
+ (ViewerController*) frontMostDisplayed2DViewer;
- (NSMutableArray*) pixList;        // DCMPix の配列（=シリーズ全スライス）
- (long) imageIndex;                // 現在表示中スライスindex
- (short) getNumberOfImages;
- (DCMView*) imageView;
@end

// ---- プラグイン基底クラス ----
@interface PluginFilter : NSObject
{
    ViewerController* viewerController;   // filterImage: 実行時に frontmost 2D viewer が入る（ivar offsetは本体が輸出）
}

+ (PluginFilter *)filter;

- (long) filterImage:(NSString*)menuName;        // メニュー選択時に呼ばれる本体
- (long) processFiles:(NSMutableArray*)files;
- (id)   report:(id)study action:(NSString*)action;

- (void) initPlugin;
- (void) willUnload;
- (BOOL) isCertifiedForMedicalImaging;
- (void) setMenus;

- (NSArray*) viewerControllersList;
- (ViewerController*) duplicateCurrent2DViewerWindow;

- (long) prepareFilter:(ViewerController*)vC;
@end
