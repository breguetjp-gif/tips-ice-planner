//
//  TIPSSendFilter.m — Miele-LXIV "Send to ICE Planner" (Database plugin)
//
//  患者リスト(Database)で選択中のスタディを、スタンドアロン版アプリ
//  「TIPS ICE Planner」へ受け渡す。
//    1. 選択スタディの DICOM ファイル一覧を取得（filesForDatabaseOutlineSelection:）
//    2. サンドボックス内の一時フォルダ(NSTemporaryDirectory=コンテナtmp)へコピー
//    3. URLスキーム tipsiceplanner://open?dir=... でアプリを起動（LaunchServices経由）
//       → 非サンドボックスのアプリがそのコンテナtmpを読み込み、自動で開く。
//
//  pluginType=Database のプラグインは「プラグイン」メニュー配下に並び、選択時に
//  filterImage: が呼ばれる（viewerController は nil、選択は BrowserController から取る）。
//  実例: pixmeo/osirixplugins の "PDF to DICOM"（DCMPDFImportFilter）と同形。
//
//  API は MieleAPI.framework の Headers が空のため、実機バイナリ(nm)と OsiriX/Horos
//  の browserController.h / PluginFilter.h から必要分だけ逆算宣言。実体は本体 exe が
//  輸出し、-bundle_loader でリンク、ロード時に dyld が結線する。
//

#import <Cocoa/Cocoa.h>

// ---- 患者リスト（データベース・ブラウザ） ----
@interface BrowserController : NSObject
+ (BrowserController*) currentBrowser;
// 返り値=選択中スタディ/シリーズの DICOM ファイルの「完全パス文字列」配列。
// 引数の可変配列には対応する DicomImage オブジェクトが入る（nil 可）。
- (NSMutableArray*) filesForDatabaseOutlineSelection:(NSMutableArray*) correspondingObjects;
- (NSArray*) databaseSelection;
@end

// ---- プラグイン基底（実体は本体 exe が提供） ----
@interface PluginFilter : NSObject
+ (PluginFilter*) filter;
- (long) filterImage:(NSString*) menuName;
@end

// ============================================================================

@interface TIPSSendFilter : PluginFilter
@end

@implementation TIPSSendFilter

static void TIPSWarn(NSString* msg)
{
    dispatch_async(dispatch_get_main_queue(), ^{
        NSAlert* a = [[NSAlert alloc] init];
        a.messageText = @"Send to ICE Planner";
        a.informativeText = msg;
        [a addButtonWithTitle:@"OK"];
        [a runModal];
    });
}

// 選択要素から実ファイルパスを取り出す（NSString パス / DicomImage どちらでも対応）
static NSString* TIPSPathOf(id item)
{
    if ([item isKindOfClass:[NSString class]])
        return (NSString*)item;
    @try {
        for (NSString* key in @[ @"completePath", @"completePathResolved", @"path" ]) {
            if ([item respondsToSelector:@selector(valueForKey:)]) {
                id v = [item valueForKey:key];
                if ([v isKindOfClass:[NSString class]] && [(NSString*)v length])
                    return (NSString*)v;
            }
        }
    } @catch (__unused NSException* e) {}
    return nil;
}

- (long) filterImage:(NSString*) menuName
{
    @autoreleasepool {
        // --- 1. 患者リストの選択を取得 ---
        Class bcCls = NSClassFromString(@"BrowserController");
        BrowserController* browser = bcCls ? [bcCls currentBrowser] : nil;
        if (browser == nil) {
            TIPSWarn(@"Could not access the patient list. Open the database window and select a study, then try again.");
            return 0;
        }

        NSMutableArray* objs = [NSMutableArray array];
        NSMutableArray* sel  = nil;
        @try { sel = [browser filesForDatabaseOutlineSelection:objs]; }
        @catch (__unused NSException* e) { sel = nil; }

        NSFileManager* fm = [NSFileManager defaultManager];
        NSMutableArray<NSString*>* paths = [NSMutableArray array];
        NSMutableSet<NSString*>* seen = [NSMutableSet set];
        for (id it in sel) {
            NSString* p = TIPSPathOf(it);
            if (p.length && ![seen containsObject:p] && [fm fileExistsAtPath:p]) {
                [seen addObject:p];
                [paths addObject:p];
            }
        }
        if (paths.count == 0) {
            TIPSWarn(@"No study is selected (or its image files were not found).\n\nSelect one study (or series) in the patient list, then choose Plugins ▸ Database ▸ Send to ICE Planner.");
            return 0;
        }

        // --- 2. サンドボックス内の一時フォルダへコピー ---
        NSString* stamp = [@"tips_handoff_" stringByAppendingString:[[NSProcessInfo processInfo] globallyUniqueString]];
        NSString* dst = [NSTemporaryDirectory() stringByAppendingPathComponent:stamp];
        NSError* err = nil;
        if (![fm createDirectoryAtPath:dst withIntermediateDirectories:YES attributes:nil error:&err]) {
            TIPSWarn([NSString stringWithFormat:@"Could not create a temporary folder:\n%@", err.localizedDescription]);
            return 0;
        }
        NSUInteger i = 0, copied = 0;
        for (NSString* src in paths) {
            NSString* base = [src lastPathComponent];
            if (base.length == 0) base = @"image";
            NSString* name = [NSString stringWithFormat:@"%06lu_%@", (unsigned long)i++, base];
            NSError* ce = nil;
            if ([fm copyItemAtPath:src toPath:[dst stringByAppendingPathComponent:name] error:&ce])
                copied++;
        }
        if (copied == 0) {
            TIPSWarn(@"Could not copy the selected images to the hand-off folder.");
            return 0;
        }

        // --- 3. URLスキームでスタンドアロン版アプリを起動 ---
        NSString* enc = [dst stringByAddingPercentEncodingWithAllowedCharacters:[NSCharacterSet URLQueryAllowedCharacterSet]];
        NSURL* url = [NSURL URLWithString:[NSString stringWithFormat:@"tipsiceplanner://open?dir=%@", enc]];

        NSWorkspaceOpenConfiguration* cfg = [NSWorkspaceOpenConfiguration configuration];
        cfg.activates = YES;
        [[NSWorkspace sharedWorkspace] openURL:url configuration:cfg
                             completionHandler:^(NSRunningApplication* app, NSError* error) {
            if (error != nil || app == nil) {
                TIPSWarn(@"Couldn't launch \"TIPS ICE Planner\".\n\n"
                          "Make sure the app is installed and has been opened at least once "
                          "(so macOS registers it), then try again.");
            }
        }];
        return 0;
    }
}

@end
