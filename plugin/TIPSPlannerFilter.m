//
//  TIPSPlannerFilter.m  — TIPS Planner（4画面ビューア + ICE向きマーカー + 可変レイアウト）
//
//  2x2: Axial / Coronal / Sagittal / ICE。各ペインは境界ドラッグで個別リサイズ可（NSSplitView）。
//  操作(Miele/OsiriX準拠):
//        左ドラッグ=階調(WL/WW) / 右ドラッグ or ピンチ=拡大縮小 / 中ドラッグ=移動(パン)
//        二本指スクロール/ホイール=断面送り(拡大中はパン, ICEはズーム) / 中ドラッグ=移動 / 右クリック=拡大・移動をリセット
//        Axialクリック=IVCパス点追加 / Cor・Sagクリック=十字位置移動
//  ICE: θ=軸まわり回転 / プローブ=パス上前後 / 偏向=先端2軸 / 90°=表示回転 / apex上・扇下(エコー標準)
//  向きマーカー: 各断面に「プローブ点＋小扇＋中心ビーム線」(琥珀色)を投影し、ICEの位置と向きを示す。
//

#import "PluginFilter.h"
#import <math.h>

typedef NS_ENUM(int, TIPSPane){ TIPSPaneAxial, TIPSPaneCoronal, TIPSPaneSagittal, TIPSPaneICE };

// 開いた Planner のコントローラ保持＋初回ディスクレーマー表示フラグ
static NSMutableArray*gTIPSControllers=nil;
static BOOL gTIPSDisclaimerShown=NO;

// ===== C ヘルパ =====
static inline float vsample(const float*vol,long N,long H,long W,long zz,double y,double x){
    if(zz<0)zz=0; else if(zz>=N)zz=N-1; if(x<0)x=0; else if(x>W-1)x=W-1; if(y<0)y=0; else if(y>H-1)y=H-1;
    long x0=(long)x,y0=(long)y,x1=(x0+1<W?x0+1:x0),y1=(y0+1<H?y0+1:y0); double fx=x-x0,fy=y-y0;
    const float*p=vol+(size_t)zz*H*W; double a=p[y0*W+x0],b=p[y0*W+x1],c=p[y1*W+x0],d=p[y1*W+x1];
    return (float)((a*(1-fx)+b*fx)*(1-fy)+(c*(1-fx)+d*fx)*fy); }
static double interp1(const double*xs,const double*ys,int K,double q){
    if(q<=xs[0])return ys[0]; if(q>=xs[K-1])return ys[K-1];
    for(int k=0;k<K-1;k++) if(q>=xs[k]&&q<=xs[k+1]){double t=(xs[k+1]!=xs[k])?(q-xs[k])/(xs[k+1]-xs[k]):0;return ys[k]*(1-t)+ys[k+1]*t;}
    return ys[K-1]; }
static NSRect aspectFit(double iw,double ih,NSRect b){ if(iw<=0||ih<=0)return b;
    double s=fmin(b.size.width/iw,b.size.height/ih),w=iw*s,h=ih*s;
    return NSMakeRect(b.origin.x+(b.size.width-w)/2,b.origin.y+(b.size.height-h)/2,w,h); }
static NSImage* grayImage(unsigned char*buf,long w,long h){
    NSBitmapImageRep*rep=[[NSBitmapImageRep alloc]initWithBitmapDataPlanes:NULL pixelsWide:w pixelsHigh:h
        bitsPerSample:8 samplesPerPixel:1 hasAlpha:NO isPlanar:NO colorSpaceName:NSDeviceWhiteColorSpace bytesPerRow:w bitsPerPixel:8];
    memcpy([rep bitmapData],buf,(size_t)w*h); NSImage*img=[[NSImage alloc]initWithSize:NSMakeSize(w,h)]; [img addRepresentation:rep]; return img; }
static unsigned char* rotateBuf(const unsigned char*src,long w,long h,int k,long*nw,long*nh){
    k=((k%4)+4)%4; unsigned char*cur=malloc((size_t)w*h);memcpy(cur,src,(size_t)w*h);long cw=w,ch=h;
    for(int s=0;s<k;s++){long dw=ch,dh=cw;unsigned char*d=malloc((size_t)dw*dh);
        for(long r=0;r<ch;r++)for(long c=0;c<cw;c++){long dr=c,dc=ch-1-r;d[(size_t)dr*dw+dc]=cur[(size_t)r*cw+c];}
        free(cur);cur=d;cw=dw;ch=dh;} *nw=cw;*nh=ch;return cur; }
// ICE画素(col,row)を rotateBuf と同じ向きに k×90°回転（W,Hも入れ替え）
static NSPoint rotPixel(NSPoint p,long*W,long*H,int k){ k=((k%4)+4)%4;
    for(int s=0;s<k;s++){ double nx=(double)(*H-1)-p.y, ny=p.x; p.x=nx; p.y=ny; long t=*W;*W=*H;*H=t; } return p; }
// rotPixel の逆（表示画素→回転前画素）。W,Hは表示時の寸法を渡す
static NSPoint invRotPixel(NSPoint p,long*W,long*H,int k){ k=((k%4)+4)%4;
    for(int s=0;s<k;s++){ double ox=p.y, oy=(double)(*W-1)-p.x; p.x=ox; p.y=oy; long t=*W;*W=*H;*H=t; } return p; }
// 3Dベクトル（カテーテル偏向の幾何用）
static void nrm3(double v[3]){ double m=sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2]); if(m>1e-9){v[0]/=m;v[1]/=m;v[2]/=m;} }
static void cross3(const double a[3],const double b[3],double o[3]){ o[0]=a[1]*b[2]-a[2]*b[1];o[1]=a[2]*b[0]-a[0]*b[2];o[2]=a[0]*b[1]-a[1]*b[0]; }
static void rot3(const double v[3],const double k[3],double a,double o[3]){ // Rodrigues: vをk軸まわりa回転
    double c=cos(a),s=sin(a),kv=k[0]*v[0]+k[1]*v[1]+k[2]*v[2];
    double cx=k[1]*v[2]-k[2]*v[1],cy=k[2]*v[0]-k[0]*v[2],cz=k[0]*v[1]-k[1]*v[0];
    o[0]=v[0]*c+cx*s+k[0]*kv*(1-c); o[1]=v[1]*c+cy*s+k[1]*kv*(1-c); o[2]=v[2]*c+cz*s+k[2]*kv*(1-c); }

// ===== 汎用ペイン =====
@interface TIPSPaneView : NSView
@property TIPSPane type; @property (strong) NSImage*img; @property double zoom;
@property double panX, panY;            // 移動(パン)オフセット(view points)
@property BOOL wheelZooms;              // YES=ホイールでズーム(ICE) / NO=断面送り(Axi/Cor/Sag)
@property long imgW,imgH; @property NSPoint cross; @property BOOL showCross;
@property (strong) NSArray*markers; @property (assign) NSRect fitRect;
@property (strong) NSArray*fanFill;     // 向きマーカー: 扇の輪郭(先頭=apex, img px)
@property (strong) NSArray*fanBeam;     // 向きマーカー: 中心ビーム線 [apex,end] (img px)
@property (strong) NSArray*pathPts;     // IVC path centerline polyline (img px, thin solid) — プローブがパス上に在ることを可視化
@property (strong) NSArray*needlePts;   // needle trajectory polyline (img px, dotted)
@property (strong) NSArray*needleCross; // needle pierce points on ICE plane (img px, x-marks)
@property (strong) NSValue*entryPt;     // Entry (img px, green dot) or nil
@property (strong) NSValue*targetPt;    // Target (img px, red dot) or nil
@property (strong) NSValue*tipPt;       // ICE probe TIP=array (img px, blue marker) — 挿入方向で決まる先端
@property BOOL entryBig,targetBig;      // YES = この断面/平面が真のEntry/Target を含む → 拡大表示
@property (copy) void(^onClick)(double ix,double iy);
@property (copy) void(^onMovePoint)(double ix,double iy,int which);   // Entry/Targetの丸を掴んでドラッグ移動 (which:1=Entry/2=Target)
@property (copy) void(^onWLWW)(double dWW,double dWL);
@property (copy) void(^onScroll)(double steps);
@property (strong) NSString*caption;
@end
@implementation TIPSPaneView { NSPoint _last; BOOL _dragged; BOOL _rdragged; NSPoint _zoomAnchor; int _grab; }
- (instancetype)initWithFrame:(NSRect)f{ if((self=[super initWithFrame:f])){_zoom=1;_showCross=NO;_panX=0;_panY=0;} return self; }
// 指定したview座標 p を中心に拡大縮小（カーソル位置で拡大）
- (void)zoomTo:(double)z2 around:(NSPoint)p{
    if(z2<0.3)z2=0.3; if(z2>8.0)z2=8.0;
    NSRect fr=[self imageRect];
    if(!self.img||fr.size.width<=0||fr.size.height<=0){ self.zoom=z2; [self setNeedsDisplay:YES]; return; }
    double u=(p.x-fr.origin.x)/fr.size.width, v=(p.y-fr.origin.y)/fr.size.height;     // カーソル下の画像内比率
    NSSize ps=self.img.size; NSRect base=aspectFit(ps.width,ps.height,self.bounds);
    double bcx=NSMidX(base), bcy=NSMidY(base);
    self.zoom=z2;
    self.panX=p.x-bcx+base.size.width*z2*(0.5-u);   // p の下に同じ画像点が残るよう pan を解く
    self.panY=p.y-bcy+base.size.height*z2*(0.5-v);
    [self clampPan]; [self setNeedsDisplay:YES];
}
// 現在の描画矩形（aspectFit＋ズーム＋パン込み）。fitRect とクリック逆変換・マーカー投影で共通使用。
- (NSRect)imageRect{
    if(!self.img)return NSZeroRect;
    NSSize ps=self.img.size; NSRect fr=aspectFit(ps.width,ps.height,self.bounds);
    if(self.zoom!=1.0){ double cx=NSMidX(fr),cy=NSMidY(fr); fr.size.width*=self.zoom;fr.size.height*=self.zoom;
        fr.origin.x=cx-fr.size.width/2; fr.origin.y=cy-fr.size.height/2; }
    fr.origin.x+=self.panX; fr.origin.y+=self.panY; return fr;
}
// 移動(パン)が行き過ぎて画像を見失わないよう、最低40pxは画面に残す
- (void)clampPan{ if(!self.img)return; NSSize ps=self.img.size; if(ps.width<=0||ps.height<=0)return;
    NSRect base=aspectFit(ps.width,ps.height,self.bounds);
    double w=base.size.width*self.zoom, h=base.size.height*self.zoom;
    double mx=(w+self.bounds.size.width)/2-40; if(mx<0)mx=0;
    double my=(h+self.bounds.size.height)/2-40; if(my<0)my=0;
    if(self.panX>mx)self.panX=mx; if(self.panX<-mx)self.panX=-mx;
    if(self.panY>my)self.panY=my; if(self.panY<-my)self.panY=-my; }
// 向きマーカー(扇＋中心線＋apex点)を描画。点は img px → view 座標へ fr で変換。
- (void)drawFanIn:(NSRect)fr{
    if(self.imgW<=0||self.imgH<=0)return;
    if(self.fanFill.count>2){
        NSBezierPath*fp=[NSBezierPath bezierPath];
        for(NSUInteger i=0;i<self.fanFill.count;i++){ NSPoint pp=[self.fanFill[i]pointValue];
            double vx=fr.origin.x+(pp.x/self.imgW)*fr.size.width, vy=fr.origin.y+(1.0-pp.y/self.imgH)*fr.size.height;
            if(i==0)[fp moveToPoint:NSMakePoint(vx,vy)]; else [fp lineToPoint:NSMakePoint(vx,vy)]; }
        [fp closePath];
        [[NSColor colorWithSRGBRed:1.0 green:0.83 blue:0.25 alpha:0.10]setFill]; [fp fill];   // ghost fill (faint)
        [[NSColor colorWithSRGBRed:1.0 green:0.83 blue:0.25 alpha:0.70]setStroke]; [fp setLineWidth:1.2]; [fp stroke];
        NSPoint a=[self.fanFill[0]pointValue];
        double ax=fr.origin.x+(a.x/self.imgW)*fr.size.width, ay=fr.origin.y+(1.0-a.y/self.imgH)*fr.size.height;
        [[NSColor colorWithSRGBRed:1.0 green:0.83 blue:0.25 alpha:1.0]setFill];
        [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(ax-3.5,ay-3.5,7,7)]fill];
    }
    if(self.fanBeam.count==2){
        NSPoint a=[self.fanBeam[0]pointValue],b=[self.fanBeam[1]pointValue];
        double ax=fr.origin.x+(a.x/self.imgW)*fr.size.width, ay=fr.origin.y+(1.0-a.y/self.imgH)*fr.size.height;
        double bx=fr.origin.x+(b.x/self.imgW)*fr.size.width, by=fr.origin.y+(1.0-b.y/self.imgH)*fr.size.height;
        NSBezierPath*bl=[NSBezierPath bezierPath]; [bl moveToPoint:NSMakePoint(ax,ay)];[bl lineToPoint:NSMakePoint(bx,by)];
        [bl setLineWidth:1.0]; CGFloat dash[2]={3,2}; [bl setLineDash:dash count:2 phase:0];
        [[NSColor colorWithSRGBRed:1.0 green:0.95 blue:0.6 alpha:0.95]setStroke]; [bl stroke];
    }
}
// 針軌道オーバーレイ（点線＋Entry/Target点＋ICE貫通点×）。点は img px → view 座標へ fr で変換。
- (NSPoint)v2:(NSPoint)pp in:(NSRect)fr{ return NSMakePoint(fr.origin.x+(pp.x/self.imgW)*fr.size.width, fr.origin.y+(1.0-pp.y/self.imgH)*fr.size.height); }
- (void)drawNeedleIn:(NSRect)fr{
    if(self.imgW<=0||self.imgH<=0)return;
    if(self.pathPts.count>1){                                   // IVCパス中心線（プローブがパス上に在ることを可視化）
        NSBezierPath*pp=[NSBezierPath bezierPath];
        for(NSUInteger i=0;i<self.pathPts.count;i++){ NSPoint v=[self v2:[self.pathPts[i]pointValue] in:fr];
            if(i==0)[pp moveToPoint:v];else[pp lineToPoint:v]; }
        [pp setLineWidth:2.0]; [[NSColor colorWithSRGBRed:0.35 green:0.80 blue:0.95 alpha:0.55]setStroke]; [pp stroke];
    }
    if(self.tipPt){ NSPoint v=[self v2:[self.tipPt pointValue] in:fr];     // ICEプローブ先端=アレイ(青)。挿入方向で決まる
        [[NSColor colorWithSRGBRed:0.30 green:0.78 blue:0.98 alpha:1.0]setFill];
        [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(v.x-5,v.y-5,10,10)]fill];
        [[NSColor whiteColor]setStroke]; NSBezierPath*o=[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(v.x-5,v.y-5,10,10)];[o setLineWidth:1.3];[o stroke];
        [@"TIP" drawAtPoint:NSMakePoint(v.x+7,v.y-6) withAttributes:@{NSForegroundColorAttributeName:[NSColor colorWithSRGBRed:0.55 green:0.85 blue:1.0 alpha:1],NSFontAttributeName:[NSFont boldSystemFontOfSize:11]}]; }
    if(self.needlePts.count>1){
        NSBezierPath*np=[NSBezierPath bezierPath];
        for(NSUInteger i=0;i<self.needlePts.count;i++){ NSPoint v=[self v2:[self.needlePts[i]pointValue] in:fr];
            if(i==0)[np moveToPoint:v];else[np lineToPoint:v]; }
        CGFloat dash[2]={5,3}; [np setLineDash:dash count:2 phase:0]; [np setLineWidth:2.0];
        [[NSColor colorWithSRGBRed:1.0 green:0.76 blue:0.30 alpha:0.95]setStroke]; [np stroke];
    }
    for(NSValue*cv in self.needleCross){ NSPoint v=[self v2:[cv pointValue] in:fr];
        [[NSColor colorWithSRGBRed:1.0 green:0.76 blue:0.30 alpha:1.0]setStroke];
        NSBezierPath*x=[NSBezierPath bezierPath]; [x setLineWidth:2.5];
        [x moveToPoint:NSMakePoint(v.x-6,v.y-6)];[x lineToPoint:NSMakePoint(v.x+6,v.y+6)];
        [x moveToPoint:NSMakePoint(v.x-6,v.y+6)];[x lineToPoint:NSMakePoint(v.x+6,v.y-6)]; [x stroke]; }
    if(self.entryPt) [self dot:[self.entryPt pointValue] in:fr color:[NSColor colorWithSRGBRed:0.40 green:0.90 blue:0.55 alpha:1.0] big:self.entryBig label:@"Entry"];
    if(self.targetPt)[self dot:[self.targetPt pointValue] in:fr color:[NSColor colorWithSRGBRed:1.0 green:0.40 blue:0.35 alpha:1.0] big:self.targetBig label:@"Target"];
}
// Entry/Target 点。big=YES（真の点を含む断面/平面）なら拡大＋外側リング＋ラベル
- (void)dot:(NSPoint)pp in:(NSRect)fr color:(NSColor*)col big:(BOOL)big label:(NSString*)lab{
    NSPoint v=[self v2:pp in:fr]; double rad=big?8.5:4.0;
    [col setFill]; [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(v.x-rad,v.y-rad,rad*2,rad*2)]fill];
    [[NSColor whiteColor]setStroke]; NSBezierPath*o=[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(v.x-rad,v.y-rad,rad*2,rad*2)]; [o setLineWidth:big?1.8:1.0]; [o stroke];
    if(big){ [col setStroke]; NSBezierPath*r2=[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(v.x-rad-4,v.y-rad-4,(rad+4)*2,(rad+4)*2)]; [r2 setLineWidth:1.5]; [r2 stroke];
        NSDictionary*at=@{NSForegroundColorAttributeName:col,NSFontAttributeName:[NSFont boldSystemFontOfSize:11]};
        [lab drawAtPoint:NSMakePoint(v.x+rad+6,v.y-6) withAttributes:at]; }
}
- (void) drawRect:(NSRect)dr {
    [[NSColor blackColor]setFill]; NSRectFill(self.bounds);
    if(self.img){ NSRect fr=[self imageRect]; self.fitRect=fr;
        [self.img drawInRect:fr fromRect:NSZeroRect operation:NSCompositingOperationCopy fraction:1.0];
        if(self.showCross&&self.imgW>0){ [[NSColor colorWithSRGBRed:0.30 green:0.85 blue:0.95 alpha:0.55]set];
            double vx=fr.origin.x+(self.cross.x/self.imgW)*fr.size.width, vy=fr.origin.y+(1.0-self.cross.y/self.imgH)*fr.size.height, g=7;
            NSBezierPath*l=[NSBezierPath bezierPath]; [l setLineWidth:1];
            [l moveToPoint:NSMakePoint(fr.origin.x,vy)];[l lineToPoint:NSMakePoint(vx-g,vy)];
            [l moveToPoint:NSMakePoint(vx+g,vy)];[l lineToPoint:NSMakePoint(NSMaxX(fr),vy)];
            [l moveToPoint:NSMakePoint(vx,fr.origin.y)];[l lineToPoint:NSMakePoint(vx,vy-g)];
            [l moveToPoint:NSMakePoint(vx,vy+g)];[l lineToPoint:NSMakePoint(vx,NSMaxY(fr))]; [l stroke]; }
        for(NSValue*v in self.markers){ NSPoint pp=[v pointValue];
            double vx=fr.origin.x+(pp.x/self.imgW)*fr.size.width, vy=fr.origin.y+(1.0-pp.y/self.imgH)*fr.size.height;
            [[NSColor colorWithSRGBRed:1.0 green:0.32 blue:0.27 alpha:0.95]set];
            NSBezierPath*c=[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(vx-5,vy-5,10,10)];[c setLineWidth:1.8];[c stroke];
            [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(vx-1.5,vy-1.5,3,3)]fill]; }
        [self drawFanIn:fr];
        [self drawNeedleIn:fr];
    }
    if(self.caption){ NSColor*acc=[NSColor colorWithSRGBRed:0.94 green:0.56 blue:0.41 alpha:1.0];
        NSDictionary*at=@{NSForegroundColorAttributeName:acc,NSFontAttributeName:[NSFont systemFontOfSize:12 weight:NSFontWeightSemibold]};
        NSSize ts=[self.caption sizeWithAttributes:at]; CGFloat bh=self.bounds.size.height;
        NSRect chip=NSMakeRect(7,bh-ts.height-9,ts.width+14,ts.height+6);
        [[NSColor colorWithWhite:0.0 alpha:0.5]setFill]; [[NSBezierPath bezierPathWithRoundedRect:chip xRadius:5 yRadius:5]fill];
        [self.caption drawAtPoint:NSMakePoint(14,bh-ts.height-6) withAttributes:at]; }
}
// img px の点が view 上で press 近傍か（Entry/Target を掴む判定）
- (BOOL)near:(NSPoint)imgpt fr:(NSRect)fr pt:(NSPoint)v{
    if(fr.size.width<=0||self.imgW<=0||self.imgH<=0)return NO;
    double vx=fr.origin.x+(imgpt.x/self.imgW)*fr.size.width, vy=fr.origin.y+(1.0-imgpt.y/self.imgH)*fr.size.height;
    return hypot(v.x-vx,v.y-vy)<15.0; }
// 左ボタン: Entry/Targetの丸を掴んでいれば移動 / それ以外は 階調(WL/WW) / クリック=onClick
- (void) mouseDown:(NSEvent*)e { _last=[self convertPoint:e.locationInWindow fromView:nil]; _dragged=NO; _grab=0;
    NSRect fr=self.fitRect;
    if(self.targetPt&&[self near:[self.targetPt pointValue] fr:fr pt:_last]) _grab=2;        // Target優先で掴む
    else if(self.entryPt&&[self near:[self.entryPt pointValue] fr:fr pt:_last]) _grab=1; }
- (void) mouseDragged:(NSEvent*)e { NSPoint p=[self convertPoint:e.locationInWindow fromView:nil];
    double dx=p.x-_last.x,dy=p.y-_last.y; if(fabs(dx)+fabs(dy)>1.5)_dragged=YES;
    if(_grab&&self.onMovePoint){ NSRect fr=self.fitRect; if(fr.size.width>0){ double ix=(p.x-fr.origin.x)/fr.size.width*self.imgW, iy=(1.0-(p.y-fr.origin.y)/fr.size.height)*self.imgH; self.onMovePoint(ix,iy,_grab);} }
    else if(self.onWLWW)self.onWLWW(dx,dy);
    _last=p; }
- (void) mouseUp:(NSEvent*)e {
    if(_dragged||_grab){ _grab=0; return; } NSRect fr=self.fitRect; NSPoint p=[self convertPoint:e.locationInWindow fromView:nil];
    if(!NSPointInRect(p,fr)||fr.size.width<=0)return; double ix=(p.x-fr.origin.x)/fr.size.width*self.imgW, iy=(1.0-(p.y-fr.origin.y)/fr.size.height)*self.imgH;
    if(self.onClick)self.onClick(ix,iy); }
// 右ボタン=拡大縮小(ドラッグ:上で拡大, OsiriX流) / リセット(クリック:ズーム1・パス0へ)
- (void) rightMouseDown:(NSEvent*)e { _last=[self convertPoint:e.locationInWindow fromView:nil]; _zoomAnchor=_last; _rdragged=NO; }
- (void) rightMouseDragged:(NSEvent*)e { NSPoint p=[self convertPoint:e.locationInWindow fromView:nil];
    double dy=p.y-_last.y; if(fabs(p.x-_last.x)+fabs(dy)>1.5)_rdragged=YES;
    [self zoomTo:self.zoom*(1.0+dy*0.005) around:_zoomAnchor];   // 押し始めた位置を中心に拡大縮小
    _last=p; }
- (void) rightMouseUp:(NSEvent*)e { if(_rdragged)return; self.zoom=1; self.panX=0; self.panY=0; [self setNeedsDisplay:YES]; }
// 中ボタン=移動(パン, マウス向け)
- (void) otherMouseDown:(NSEvent*)e { _last=[self convertPoint:e.locationInWindow fromView:nil]; }
- (void) otherMouseDragged:(NSEvent*)e { NSPoint p=[self convertPoint:e.locationInWindow fromView:nil];
    self.panX+=p.x-_last.x; self.panY+=p.y-_last.y; _last=p; [self clampPan]; [self setNeedsDisplay:YES]; }
- (void) scrollWheel:(NSEvent*)e {
    if(self.wheelZooms){ double s=(e.scrollingDeltaY!=0?e.scrollingDeltaY:e.deltaY);   // ICE: スクロール=ズーム(カーソル中心)
        NSPoint p=[self convertPoint:e.locationInWindow fromView:nil];
        [self zoomTo:self.zoom*(s>0?1.08:0.926) around:p]; return; }
    if(self.zoom!=1.0 && e.hasPreciseScrollingDeltas){                                   // 拡大中のトラックパッド二本指=移動(パン)
        self.panX+=e.scrollingDeltaX; self.panY+=e.scrollingDeltaY; [self clampPan]; [self setNeedsDisplay:YES]; return; }
    double s=(e.scrollingDeltaY!=0?e.scrollingDeltaY:e.deltaY);                           // 等倍 or マウスホイール=断面送り
    if(self.onScroll)self.onScroll(s); }
// トラックパッドのピンチ=拡大縮小（全ペイン共通。マウス無しでもズーム可）
- (void) magnifyWithEvent:(NSEvent*)e { NSPoint p=[self convertPoint:e.locationInWindow fromView:nil]; [self zoomTo:self.zoom*(1.0+e.magnification) around:p]; }   // ピンチ=カーソル中心ズーム
@end

// ===== ペインのセル：ペイン＋縦スライダーを内包し、リサイズ時に再配置 =====
@interface TIPSPaneCell : NSView
@property (strong) TIPSPaneView*pane; @property (strong) NSSlider*slider;
- (void)relayout;
@end
@implementation TIPSPaneCell
- (void)resizeSubviewsWithOldSize:(NSSize)old { [self relayout]; }
- (void)relayout {
    NSRect b=self.bounds; CGFloat sw=16, sg=2;
    if(self.slider){ self.pane.frame=NSMakeRect(0,0,fmax(0,b.size.width-sw-sg),b.size.height);
                     self.slider.frame=NSMakeRect(fmax(0,b.size.width-sw),0,sw,b.size.height); }
    else if(self.pane){ self.pane.frame=b; }
}
@end

// ===== 視認・把持しやすい分割線（テラコッタ色・6px）=====
@interface TIPSSplitView : NSSplitView
@end
@implementation TIPSSplitView
- (CGFloat)dividerThickness { return 6.0; }
- (NSColor*)dividerColor { return [NSColor colorWithSRGBRed:0.94 green:0.56 blue:0.41 alpha:0.55]; }
@end

// ===== 連動3Dビュー（Core Graphics・自由回転）=====
typedef struct { double x,y,z; } P3;
static NSValue* boxP3(P3 p){ return [NSValue valueWithBytes:&p objCType:@encode(P3)]; }
static P3 unboxP3(NSValue*v){ P3 p={0,0,0}; [v getValue:&p]; return p; }
@interface TIPS3DPane : NSView
@property double az,el;                                  // 視点 方位/仰角
@property (strong) NSArray*pathPts,*fanPoly,*needlePts;  // P3 boxed
@property (strong) NSArray*tipSeg,*arraySeg;             // P3 boxed [base,apex]: 剛体先端(橙) / アレイ前面パッチ(青)
@property (strong) NSValue*entryPt,*targetPt,*apexPt,*beamEnd; // P3 boxed
@property double theta,b1,b2,probeFrac;                  // 手元④軸ダイヤル値
@property BOOL valid; @property (strong) NSString*caption;
@end
@implementation TIPS3DPane { NSPoint _last; }
- (instancetype)initWithFrame:(NSRect)f{ if((self=[super initWithFrame:f])){_az=-60;_el=20;} return self; }
- (NSPoint)proj:(P3)p c:(P3)c s:(double)s{
    double a=_az*M_PI/180.0,e=_el*M_PI/180.0, x=p.x-c.x,y=p.y-c.y,z=p.z-c.z;
    double X=cos(a)*x - sin(a)*y, Y=sin(a)*x + cos(a)*y;
    double Y2=cos(e)*Y - sin(e)*z;                       // 仰角チルト
    NSRect b=self.bounds; return NSMakePoint(NSMidX(b)+X*s, NSMidY(b)-Y2*s); }
- (void)stroke:(NSArray*)pts c:(P3)c s:(double)sc color:(NSColor*)col w:(CGFloat)w dash:(BOOL)d{
    if(pts.count<2)return; NSBezierPath*bp=[NSBezierPath bezierPath];
    for(NSUInteger i=0;i<pts.count;i++){ NSPoint v=[self proj:unboxP3(pts[i]) c:c s:sc]; if(i==0)[bp moveToPoint:v];else[bp lineToPoint:v]; }
    [bp setLineWidth:w]; if(d){CGFloat dd[2]={5,3};[bp setLineDash:dd count:2 phase:0];} [col setStroke]; [bp stroke]; }
- (void)dot:(NSValue*)v c:(P3)c s:(double)sc color:(NSColor*)col r:(CGFloat)r{
    if(!v)return; NSPoint p=[self proj:unboxP3(v) c:c s:sc]; [col setFill]; [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(p.x-r,p.y-r,r*2,r*2)]fill];
    [[NSColor whiteColor]setStroke]; NSBezierPath*o=[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(p.x-r,p.y-r,r*2,r*2)];[o setLineWidth:1];[o stroke]; }
- (void)drawRect:(NSRect)dr{
    [[NSColor colorWithSRGBRed:0.07 green:0.10 blue:0.14 alpha:1]setFill]; NSRectFill(self.bounds);
    if(self.caption){ NSDictionary*at=@{NSForegroundColorAttributeName:[NSColor colorWithSRGBRed:0.94 green:0.56 blue:0.41 alpha:1],NSFontAttributeName:[NSFont systemFontOfSize:12 weight:NSFontWeightSemibold]};
        [self.caption drawAtPoint:NSMakePoint(10,self.bounds.size.height-20) withAttributes:at]; }
    if(!self.valid){ NSDictionary*at=@{NSForegroundColorAttributeName:[NSColor grayColor],NSFontAttributeName:[NSFont systemFontOfSize:12]};
        [@"Draw the ICE path to show 3D" drawAtPoint:NSMakePoint(10,self.bounds.size.height/2) withAttributes:at]; return; }
    P3 c=self.apexPt?unboxP3(self.apexPt):(P3){0,0,0};
    double s=fmin(self.bounds.size.width,self.bounds.size.height)/210.0;
    // 扇(半透明)
    if(self.fanPoly.count>2){ NSBezierPath*fp=[NSBezierPath bezierPath];
        for(NSUInteger i=0;i<self.fanPoly.count;i++){NSPoint v=[self proj:unboxP3(self.fanPoly[i]) c:c s:s];if(i==0)[fp moveToPoint:v];else[fp lineToPoint:v];} [fp closePath];
        [[NSColor colorWithSRGBRed:0.22 green:0.78 blue:0.85 alpha:0.18]setFill];[fp fill];
        [[NSColor colorWithSRGBRed:1.0 green:0.85 blue:0.4 alpha:0.8]setStroke];[fp setLineWidth:1.2];[fp stroke]; }
    [self stroke:self.pathPts c:c s:s color:[NSColor colorWithSRGBRed:0.80 green:0.85 blue:0.9 alpha:0.95] w:5 dash:NO];   // カテーテル(シャフト)=灰
    [self stroke:self.tipSeg c:c s:s color:[NSColor colorWithSRGBRed:0.96 green:0.55 blue:0.20 alpha:1] w:8 dash:NO];      // 偏向で曲がる先端=オレンジ
    [self stroke:self.arraySeg c:c s:s color:[NSColor colorWithSRGBRed:0.25 green:0.72 blue:0.98 alpha:1] w:5 dash:NO];    // 先端前面アレイ=青
    if(self.beamEnd&&self.apexPt)[self stroke:@[self.apexPt,self.beamEnd] c:c s:s color:[NSColor colorWithSRGBRed:1.0 green:0.95 blue:0.6 alpha:0.6] w:1 dash:YES]; // 中心ビーム
    [self stroke:self.needlePts c:c s:s color:[NSColor colorWithSRGBRed:1.0 green:0.76 blue:0.30 alpha:0.95] w:2.4 dash:YES];  // 針
    [self dot:self.apexPt c:c s:s color:[NSColor colorWithSRGBRed:0.30 green:0.78 blue:0.98 alpha:1] r:5];                 // アレイ(青)
    [self dot:self.entryPt c:c s:s color:[NSColor colorWithSRGBRed:0.40 green:0.90 blue:0.55 alpha:1] r:5];
    [self dot:self.targetPt c:c s:s color:[NSColor colorWithSRGBRed:1.0 green:0.40 blue:0.35 alpha:1] r:5];
    [self label:@"Array" at:self.apexPt c:c s:s color:[NSColor colorWithSRGBRed:0.6 green:0.85 blue:1.0 alpha:1]];
    [self label:@"Entry" at:self.entryPt c:c s:s color:[NSColor colorWithSRGBRed:0.55 green:0.95 blue:0.65 alpha:1]];
    [self label:@"Target" at:self.targetPt c:c s:s color:[NSColor colorWithSRGBRed:1.0 green:0.55 blue:0.5 alpha:1]];
    [self drawHandle];
    NSDictionary*h=@{NSForegroundColorAttributeName:[NSColor colorWithWhite:0.62 alpha:1],NSFontAttributeName:[NSFont systemFontOfSize:9]};
    [@"gray=shaft / orange=deflectable tip / blue=array / cyan=ICE sector / dashed=needle    drag to rotate" drawAtPoint:NSMakePoint(10,8) withAttributes:h]; }
// 手元④軸ダイヤル（θ / A-P / L-R / Probe）を右下にコンパクト表示
- (void)miniDial:(NSPoint)c r:(double)r val:(double)v full:(double)full label:(NSString*)l{
    [[NSColor colorWithWhite:0.30 alpha:1]setStroke]; NSBezierPath*o=[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(c.x-r,c.y-r,2*r,2*r)];[o setLineWidth:1.4];[o stroke];
    double ang=M_PI/2.0 - (v/full)*2.0*M_PI;
    [[NSColor colorWithSRGBRed:1.0 green:0.72 blue:0.3 alpha:1]setStroke]; NSBezierPath*n=[NSBezierPath bezierPath];[n moveToPoint:c];[n lineToPoint:NSMakePoint(c.x+r*0.8*cos(ang),c.y+r*0.8*sin(ang))];[n setLineWidth:2];[n stroke];
    NSDictionary*a=@{NSForegroundColorAttributeName:[NSColor colorWithWhite:0.7 alpha:1],NSFontAttributeName:[NSFont systemFontOfSize:8]};
    [l drawAtPoint:NSMakePoint(c.x-r,c.y-r-11) withAttributes:a]; }
- (void)drawHandle{
    NSRect b=self.bounds; double ox=b.size.width-186, oy=20;
    [self miniDial:NSMakePoint(ox+16,oy+16) r:13 val:self.theta full:360 label:@"θ"];
    [self miniDial:NSMakePoint(ox+54,oy+16) r:13 val:self.b1+80 full:160 label:@"A/P"];
    [self miniDial:NSMakePoint(ox+92,oy+16) r:13 val:self.b2+80 full:160 label:@"L/R"];
    double tx=ox+128; [[NSColor colorWithWhite:0.30 alpha:1]setStroke];NSBezierPath*t=[NSBezierPath bezierPath];[t moveToPoint:NSMakePoint(tx,oy+2)];[t lineToPoint:NSMakePoint(tx,oy+30)];[t setLineWidth:2];[t stroke];
    [[NSColor colorWithSRGBRed:1.0 green:0.72 blue:0.3 alpha:1]setFill];[[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(tx-3,oy+2+28*self.probeFrac-3,6,6)]fill];
    NSDictionary*a=@{NSForegroundColorAttributeName:[NSColor colorWithWhite:0.7 alpha:1],NSFontAttributeName:[NSFont systemFontOfSize:8]};[@"Probe" drawAtPoint:NSMakePoint(tx-10,oy-9) withAttributes:a];
    NSDictionary*vv=@{NSForegroundColorAttributeName:[NSColor colorWithSRGBRed:1.0 green:0.85 blue:0.5 alpha:1],NSFontAttributeName:[NSFont boldSystemFontOfSize:9]};
    [[NSString stringWithFormat:@"θ%.0f A/P%+.0f L/R%+.0f",self.theta,self.b1,self.b2] drawAtPoint:NSMakePoint(ox,oy+34) withAttributes:vv]; }
- (void)label:(NSString*)t at:(NSValue*)v c:(P3)c s:(double)sc color:(NSColor*)col{
    if(!v)return; NSPoint p=[self proj:unboxP3(v) c:c s:sc];
    [t drawAtPoint:NSMakePoint(p.x+7,p.y-5) withAttributes:@{NSForegroundColorAttributeName:col,NSFontAttributeName:[NSFont boldSystemFontOfSize:10]}]; }
- (void)mouseDown:(NSEvent*)e{ _last=[self convertPoint:e.locationInWindow fromView:nil]; }
- (void)mouseDragged:(NSEvent*)e{ NSPoint p=[self convertPoint:e.locationInWindow fromView:nil];
    _az+=(p.x-_last.x)*0.6; _el+=(p.y-_last.y)*0.6; if(_el>89)_el=89;if(_el<-89)_el=-89; _last=p; [self setNeedsDisplay:YES]; }
@end

// ===== コントローラ =====
@interface TIPSPlannerController : NSObject <NSWindowDelegate, NSSplitViewDelegate>
@end
@implementation TIPSPlannerController {
    float*_vol; long _N,_H,_W; double _dz,_sx,_sy;
    long _cx,_cy,_cz; double _wl,_ww; int _rot; BOOL _iceFlip;   // _iceFlip=ICE左右反転
    NSMutableArray*_points;
    NSWindow*_win; TIPSPaneView*_pAx,*_pCor,*_pSag,*_pIce; TIPS3DPane*_p3d;
    NSSlider*_thetaSlider,*_probeSlider,*_zSlider,*_ySlider,*_xSlider,*_b1Slider,*_b2Slider; NSTextField*_info; BOOL _probeInit;
    double _fanTip[3],_fanVp[3],_fanSp[3],_fanHalf,_fanR;                   // ICE orientation-marker geometry (set by iceImg)
    double _fanS[3],_fanA[3],_fanFb[3];                                     // shaft tangent / array axis / fulcrum (for 3D & needle panels)
    BOOL _fanValid; long _iceWi,_iceHi;                                     // ICE scan-conversion pixel dims (pre-rotation)
    // --- needle planning ---
    double _entry[3],_target[3]; BOOL _hasEntry,_hasTarget;
    int _step,_ptMode;                                                     // step 0=ICE setup/1=Needle ; ptMode 0=Entry/1=Target （針はタイプ無し=Curve角度のみで一本化）
    BOOL _tipHighZ;                                                         // 先端(TIP=アレイ)が高z端か（挿入方向で決まる: Femoral=YES/Jugular=NO）
    NSSlider*_needleAngleSlider; NSTextField*_bendVal,*_curveLbl,*_step1Hint,*_step1Hint2,*_b1Val,*_b2Val;
    NSButton*_step1Btn,*_step2Btn,*_entryBtn,*_targetBtn,*_femBtn,*_jugBtn,*_clrNBtn;  // 視認性の高いトグルボタン
}
- (instancetype)initWithVol:(float*)vol N:(long)N H:(long)H W:(long)W dz:(double)dz sx:(double)sx sy:(double)sy{
    if((self=[super init])){_vol=vol;_N=N;_H=H;_W=W;_dz=dz;_sx=sx;_sy=sy;
        _cx=W/2;_cy=H/2;_cz=N/2;_wl=40;_ww=400;_rot=0;_points=[NSMutableArray array];_fanValid=NO;
        [self buildWindow];[self refreshAll];}
    return self; }
- (NSTextField*)label:(NSString*)s frame:(NSRect)f{ NSTextField*t=[[NSTextField alloc]initWithFrame:f];
    t.editable=NO;t.bordered=NO;t.drawsBackground=NO;t.textColor=[NSColor colorWithWhite:0.80 alpha:1];t.font=[NSFont systemFontOfSize:11];t.stringValue=s;return t; }
- (NSTextField*)acc:(NSString*)s frame:(NSRect)f{ NSTextField*t=[self label:s frame:f];
    t.textColor=[NSColor colorWithSRGBRed:0.94 green:0.56 blue:0.41 alpha:1];t.font=[NSFont systemFontOfSize:11 weight:NSFontWeightSemibold];return t; }
- (NSSlider*)vslider:(double)maxv val:(long)v{ NSSlider*s=[[NSSlider alloc]initWithFrame:NSMakeRect(0,0,16,100)];
    s.vertical=YES; s.minValue=0; s.maxValue=(maxv>0?maxv:1); s.integerValue=v; s.target=self; s.action=@selector(navChanged:); s.appearance=[NSAppearance appearanceNamed:NSAppearanceNameAqua]; return s; }
// 視認性の高いトグルボタン（押下でON/OFF、ラジオ的に使う）
- (NSButton*)toggle:(NSString*)title frame:(NSRect)f action:(SEL)a{
    NSButton*b=[[NSButton alloc]initWithFrame:f]; b.title=title; b.bezelStyle=NSBezelStyleRounded;
    [b setButtonType:NSButtonTypePushOnPushOff]; b.font=[NSFont systemFontOfSize:11];
    b.target=self; b.action=a; b.autoresizingMask=NSViewMaxYMargin; return b; }
- (TIPSPaneCell*)cellWithPane:(TIPSPaneView*)p slider:(NSSlider*)sl{
    TIPSPaneCell*cell=[[TIPSPaneCell alloc]initWithFrame:NSMakeRect(0,0,200,200)]; cell.autoresizesSubviews=YES;
    cell.pane=p; cell.slider=sl; [cell addSubview:p]; if(sl)[cell addSubview:sl]; [cell relayout]; return cell; }

- (void)buildWindow {
    CGFloat W=1160,Hh=910, ctrlH=158;
    _win=[[NSWindow alloc]initWithContentRect:NSMakeRect(40,40,W,Hh)
        styleMask:(NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskResizable|NSWindowStyleMaskMiniaturizable)
        backing:NSBackingStoreBuffered defer:NO];
    _win.title=[NSString stringWithFormat:@"TIPS Planner (vol %ld×%ld×%ld, %.2f×%.2f×%.2fmm)",_N,_H,_W,_sx,_sy,_dz];
    _win.delegate=self;[_win setReleasedWhenClosed:NO];[_win setContentMinSize:NSMakeSize(720,560)];
    NSView*c=[[NSView alloc]initWithFrame:NSMakeRect(0,0,W,Hh)]; c.autoresizesSubviews=YES;
    c.wantsLayer=YES; c.layer.backgroundColor=[[NSColor colorWithSRGBRed:0.06 green:0.07 blue:0.09 alpha:1]CGColor];
    __weak TIPSPlannerController*ws=self;

    // ---- 下部コントロールバー（底に固定）----
    NSView*ctrl=[[NSView alloc]initWithFrame:NSMakeRect(0,0,W,ctrlH)];
    ctrl.autoresizingMask=NSViewWidthSizable|NSViewMaxYMargin; ctrl.autoresizesSubviews=YES;
    NSView*bar=[[NSView alloc]initWithFrame:NSMakeRect(0,0,W,ctrlH-2)]; bar.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;
    bar.wantsLayer=YES; bar.layer.backgroundColor=[[NSColor colorWithSRGBRed:0.105 green:0.12 blue:0.15 alpha:1]CGColor]; [ctrl addSubview:bar];

    NSTextField*titleAcc=[self acc:@"TIPS Planner — ICE Puncture Planner" frame:NSMakeRect(860,100,290,18)];
    titleAcc.autoresizingMask=NSViewMinXMargin|NSViewMaxYMargin; [ctrl addSubview:titleAcc];

    NSAppearance*aqua=[NSAppearance appearanceNamed:NSAppearanceNameAqua];   // スライダーのトラックを暗背景でも見えるよう明色化
    // Row 1: Rotate θ / Probe（Probe端に Caudal(足側)・Cranial(頭側)）
    NSTextField*lTheta=[self acc:@"Rotate θ" frame:NSMakeRect(10,100,58,16)]; lTheta.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:lTheta];
    _thetaSlider=[[NSSlider alloc]initWithFrame:NSMakeRect(70,98,250,18)];_thetaSlider.minValue=0;_thetaSlider.maxValue=360;_thetaSlider.doubleValue=180; _thetaSlider.appearance=aqua;
    _thetaSlider.target=self;_thetaSlider.action=@selector(ctrlChanged:);_thetaSlider.autoresizingMask=NSViewMaxYMargin;[ctrl addSubview:_thetaSlider];
    NSTextField*lProbe=[self acc:@"Probe" frame:NSMakeRect(332,100,40,16)]; lProbe.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:lProbe];
    NSTextField*lCaud=[self label:@"Caudal" frame:NSMakeRect(372,100,44,16)]; lCaud.font=[NSFont systemFontOfSize:9]; lCaud.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:lCaud];
    _probeSlider=[[NSSlider alloc]initWithFrame:NSMakeRect(418,98,168,18)];_probeSlider.minValue=0;_probeSlider.maxValue=1;_probeSlider.doubleValue=0.5;_probeSlider.enabled=NO; _probeSlider.appearance=aqua;
    _probeSlider.target=self;_probeSlider.action=@selector(probeChanged:);_probeSlider.autoresizingMask=NSViewMaxYMargin;[ctrl addSubview:_probeSlider];
    NSTextField*lCran=[self label:@"Cranial" frame:NSMakeRect(590,100,48,16)]; lCran.font=[NSFont systemFontOfSize:9]; lCran.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:lCran];
    // Row 2: two deflection axes (AcuNav 4-way A/P + L/R)。角度はスライダー隣に表示
    NSTextField*lB1=[self acc:@"Deflect A/P" frame:NSMakeRect(10,74,78,16)]; lB1.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:lB1];
    _b1Slider=[[NSSlider alloc]initWithFrame:NSMakeRect(90,72,180,18)];_b1Slider.minValue=-80;_b1Slider.maxValue=80;_b1Slider.doubleValue=0; _b1Slider.appearance=aqua;
    _b1Slider.target=self;_b1Slider.action=@selector(ctrlChanged:);_b1Slider.autoresizingMask=NSViewMaxYMargin;[ctrl addSubview:_b1Slider];
    _b1Val=[self acc:@"0°" frame:NSMakeRect(274,74,44,16)]; _b1Val.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:_b1Val];
    NSTextField*lB2=[self acc:@"Deflect L/R" frame:NSMakeRect(332,74,78,16)]; lB2.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:lB2];
    _b2Slider=[[NSSlider alloc]initWithFrame:NSMakeRect(414,72,180,18)];_b2Slider.minValue=-80;_b2Slider.maxValue=80;_b2Slider.doubleValue=0; _b2Slider.appearance=aqua;
    _b2Slider.target=self;_b2Slider.action=@selector(ctrlChanged:);_b2Slider.autoresizingMask=NSViewMaxYMargin;[ctrl addSubview:_b2Slider];
    _b2Val=[self acc:@"0°" frame:NSMakeRect(598,74,44,16)]; _b2Val.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:_b2Val];
    // ボタン
    NSButton*rotb=[[NSButton alloc]initWithFrame:NSMakeRect(656,96,96,22)];rotb.title=@"Rotate 90°";rotb.bezelStyle=NSBezelStyleRounded;rotb.target=self;rotb.action=@selector(rotate90:);rotb.autoresizingMask=NSViewMaxYMargin;[ctrl addSubview:rotb];
    NSButton*clr=[[NSButton alloc]initWithFrame:NSMakeRect(656,70,128,22)];clr.title=@"Reset path";clr.bezelStyle=NSBezelStyleRounded;clr.target=self;clr.action=@selector(clearPath:);clr.autoresizingMask=NSViewMaxYMargin;clr.toolTip=@"Clear the clicked IVC path and start over";[clr sizeToFit];[ctrl addSubview:clr];
    NSButton*ctr=[[NSButton alloc]initWithFrame:NSMakeRect(758,96,110,22)];ctr.title=@"Zero deflection";ctr.bezelStyle=NSBezelStyleRounded;ctr.target=self;ctr.action=@selector(centerDeflect:);ctr.autoresizingMask=NSViewMaxYMargin;[ctr sizeToFit];[ctrl addSubview:ctr];
    NSButton*flb=[[NSButton alloc]initWithFrame:NSMakeRect(792,70,110,22)];flb.title=@"Flip L/R (ICE)";flb.bezelStyle=NSBezelStyleRounded;flb.target=self;flb.action=@selector(flipICE:);flb.autoresizingMask=NSViewMaxYMargin;[flb sizeToFit];[ctrl addSubview:flb];
    _info=[self label:@"" frame:NSMakeRect(10,42,1140,18)];_info.font=[NSFont systemFontOfSize:11];_info.autoresizingMask=NSViewWidthSizable|NSViewMaxYMargin;[ctrl addSubview:_info];
    NSTextField*help=[self label:@"Left-drag = window level  ·  Right-drag/pinch/wheel = zoom (at cursor)  ·  Right-click = reset  ·  Two-finger scroll = slice  ·  Middle-drag = pan  ·  Click Axial = IVC path / Entry / Target (per selector)  ·  Drag borders = resize panes"
        frame:NSMakeRect(10,20,1140,16)]; help.autoresizingMask=NSViewWidthSizable|NSViewMaxYMargin; [ctrl addSubview:help];
    NSTextField*dis=[self label:@"⚠ Research / education / self-training prototype  ·  Not a medical device  ·  Not intraprocedural navigation  ·  The operator makes all final clinical decisions"
        frame:NSMakeRect(10,3,1140,15)];
    dis.textColor=[NSColor colorWithSRGBRed:0.95 green:0.72 blue:0.40 alpha:0.95]; dis.font=[NSFont systemFontOfSize:10 weight:NSFontWeightMedium]; dis.autoresizingMask=NSViewWidthSizable|NSViewMaxYMargin; [ctrl addSubview:dis];

    // ---- Row 0 (top): step-based controls. Step1=ICE setup / Step2=Needle（表示はステップで切替）----
    NSTextField*lStep=[self acc:@"Step" frame:NSMakeRect(6,131,30,16)]; lStep.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:lStep];
    _step1Btn=[self toggle:@"1. ICE setup" frame:NSMakeRect(38,128,108,22) action:@selector(goStep1:)]; [ctrl addSubview:_step1Btn];
    _step2Btn=[self toggle:@"2. Needle" frame:NSMakeRect(150,128,92,22) action:@selector(goStep2:)]; [ctrl addSubview:_step2Btn];
    // Step1: 挿入方向（先端=TIPの確定 → 屈曲方向が正しくなる）
    _step1Hint=[self acc:@"Insertion" frame:NSMakeRect(256,131,62,16)]; _step1Hint.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:_step1Hint];
    _femBtn=[self toggle:@"Femoral (foot)" frame:NSMakeRect(320,128,150,22) action:@selector(useFemoral:)]; [ctrl addSubview:_femBtn];
    _jugBtn=[self toggle:@"Jugular (neck)" frame:NSMakeRect(474,128,150,22) action:@selector(useJugular:)]; [ctrl addSubview:_jugBtn];
    _step1Hint2=[self acc:@"Click Axial to draw the path · TIP marker shows the tip" frame:NSMakeRect(632,131,330,16)]; _step1Hint2.textColor=[NSColor colorWithWhite:0.6 alpha:1]; _step1Hint2.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:_step1Hint2];
    // Step2: Entry/Target/針種/Curve（Step1と同じ場所に重ね、表示切替）
    _entryBtn=[self toggle:@"Entry" frame:NSMakeRect(256,128,64,22) action:@selector(setEntryMode:)]; [ctrl addSubview:_entryBtn];
    _targetBtn=[self toggle:@"Target" frame:NSMakeRect(322,128,70,22) action:@selector(setTargetMode:)]; [ctrl addSubview:_targetBtn];
    // 針はタイプ廃止＝Curve角度のみで一本化（0°=直線, 正負で弓なり方向）。レンジ -20°〜+30°, 既定 +20°
    _curveLbl=[self acc:@"Needle curve" frame:NSMakeRect(400,131,86,16)]; _curveLbl.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:_curveLbl];
    _needleAngleSlider=[[NSSlider alloc]initWithFrame:NSMakeRect(490,128,250,22)]; _needleAngleSlider.minValue=-20;_needleAngleSlider.maxValue=30;_needleAngleSlider.doubleValue=20; _needleAngleSlider.appearance=[NSAppearance appearanceNamed:NSAppearanceNameAqua];
    _needleAngleSlider.target=self; _needleAngleSlider.action=@selector(ctrlChanged:); _needleAngleSlider.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:_needleAngleSlider];
    _bendVal=[self acc:@"+20°" frame:NSMakeRect(744,131,44,16)]; _bendVal.autoresizingMask=NSViewMaxYMargin; [ctrl addSubview:_bendVal];
    _clrNBtn=[[NSButton alloc]initWithFrame:NSMakeRect(822,128,118,22)];_clrNBtn.title=@"Clear needle";_clrNBtn.bezelStyle=NSBezelStyleRounded;_clrNBtn.target=self;_clrNBtn.action=@selector(clearNeedle:);_clrNBtn.autoresizingMask=NSViewMaxYMargin;[_clrNBtn sizeToFit];[ctrl addSubview:_clrNBtn];
    _step=0;_ptMode=0;_tipHighZ=YES; [self updateModeButtons];
    [c addSubview:ctrl];

    // ---- 2x2 グリッド（NSSplitView 入れ子：個別リサイズ＋ウィンドウ追従）----
    NSRect gridFrame=NSMakeRect(0,ctrlH,W,Hh-ctrlH);
    NSSplitView*outer=[[TIPSSplitView alloc]initWithFrame:gridFrame];
    outer.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable; outer.vertical=NO; outer.dividerStyle=NSSplitViewDividerStyleThin; outer.delegate=self;
    NSSplitView*top=[[TIPSSplitView alloc]initWithFrame:NSMakeRect(0,0,gridFrame.size.width,gridFrame.size.height/2)];
    top.vertical=YES; top.dividerStyle=NSSplitViewDividerStyleThin; top.delegate=self;
    NSSplitView*bot=[[TIPSSplitView alloc]initWithFrame:NSMakeRect(0,0,gridFrame.size.width,gridFrame.size.height/2)];
    bot.vertical=YES; bot.dividerStyle=NSSplitViewDividerStyleThin; bot.delegate=self;

    _pAx =[[TIPSPaneView alloc]initWithFrame:NSZeroRect]; _pAx.type=TIPSPaneAxial; _pAx.caption=@"Axial (click = IVC path point)";
    _pCor=[[TIPSPaneView alloc]initWithFrame:NSZeroRect];_pCor.type=TIPSPaneCoronal;_pCor.caption=@"Coronal";
    _pSag=[[TIPSPaneView alloc]initWithFrame:NSZeroRect];_pSag.type=TIPSPaneSagittal;_pSag.caption=@"Sagittal";
    _pIce=[[TIPSPaneView alloc]initWithFrame:NSZeroRect];_pIce.type=TIPSPaneICE;_pIce.caption=@"ICE view (wheel = Rotate θ)";   // ホイール=θ回転（拡大はピンチ/右ドラッグ）
    for(TIPSPaneView*p in @[_pAx,_pCor,_pSag,_pIce]) p.onWLWW=^(double dWW,double dWL){ [ws adjustWL:dWL ww:dWW]; };
    _pAx.onClick =^(double ix,double iy){ [ws axialClick:ix y:iy]; };
    _pCor.onClick=^(double ix,double iy){ [ws coronalClick:ix y:iy]; };
    _pSag.onClick=^(double ix,double iy){ [ws sagittalClick:ix y:iy]; };
    _pIce.onClick=^(double ix,double iy){ [ws iceClick:ix y:iy]; };   // ICEクリック→ICE平面上の点をEntry/Targetに
    _pAx.onMovePoint =^(double ix,double iy,int w){ [ws movePoint:w to:ix y:iy plane:0]; };   // Entry/Targetの丸を掴んでドラッグ移動
    _pCor.onMovePoint=^(double ix,double iy,int w){ [ws movePoint:w to:ix y:iy plane:1]; };
    _pSag.onMovePoint=^(double ix,double iy,int w){ [ws movePoint:w to:ix y:iy plane:2]; };
    _pIce.onMovePoint=^(double ix,double iy,int w){ [ws movePoint:w to:ix y:iy plane:3]; };
    _pAx.onScroll =^(double s){ [ws scrollPlane:0 by:s]; };
    _pCor.onScroll=^(double s){ [ws scrollPlane:1 by:s]; };
    _pSag.onScroll=^(double s){ [ws scrollPlane:2 by:s]; };
    _pIce.onScroll=^(double s){ [ws rotateThetaBy:s]; };   // ICEホイール=θ回転

    _zSlider=[self vslider:_N-1 val:_cz]; _ySlider=[self vslider:_H-1 val:_cy]; _xSlider=[self vslider:_W-1 val:_cx];
    TIPSPaneCell*axCell =[self cellWithPane:_pAx  slider:_zSlider];
    TIPSPaneCell*corCell=[self cellWithPane:_pCor slider:_ySlider];
    TIPSPaneCell*sagCell=[self cellWithPane:_pSag slider:_xSlider];
    TIPSPaneCell*iceCell=[self cellWithPane:_pIce slider:nil];
    _p3d=[[TIPS3DPane alloc]initWithFrame:NSZeroRect]; _p3d.caption=@"3D linkage (drag to rotate)";   // 連動3D
    [top addSubview:axCell]; [top addSubview:corCell];     // 上段: Axial | Coronal
    [bot addSubview:sagCell]; [bot addSubview:iceCell]; [bot addSubview:_p3d];   // 下段: Sagittal | ICE | 3D
    [outer addSubview:top]; [outer addSubview:bot];        // 上段 / 下段
    [c addSubview:outer];

    _win.contentView=c;[_win center];[_win makeKeyAndOrderFront:nil];
    // 初期 50/50（ウィンドウ表示後に分割位置を確定）
    [outer adjustSubviews]; [top adjustSubviews]; [bot adjustSubviews];
    [top setPosition:gridFrame.size.width/2 ofDividerAtIndex:0];
    [bot setPosition:gridFrame.size.width/3 ofDividerAtIndex:0];          // 下段は Sag | ICE | 3D の3分割
    [bot setPosition:gridFrame.size.width*2/3 ofDividerAtIndex:1];
    [outer setPosition:gridFrame.size.height/2 ofDividerAtIndex:0];
    [self updateInfo];
}

// 各ペイン最小サイズ（境界ドラッグ時に潰れない）
- (CGFloat)splitView:(NSSplitView*)sv constrainMinCoordinate:(CGFloat)p ofSubviewAt:(NSInteger)i { return p+130; }
- (CGFloat)splitView:(NSSplitView*)sv constrainMaxCoordinate:(CGFloat)p ofSubviewAt:(NSInteger)i { return p-130; }

- (void)updateInfo{
    if(_bendVal)_bendVal.stringValue=[NSString stringWithFormat:@"%+.0f°",_needleAngleSlider.doubleValue];
    if(_b1Val)_b1Val.stringValue=[NSString stringWithFormat:@"%+.0f°",_b1Slider.doubleValue];   // 偏向角をスライダー隣に表示
    if(_b2Val)_b2Val.stringValue=[NSString stringWithFormat:@"%+.0f°",_b2Slider.doubleValue];
    NSString*nd=(_hasEntry||_hasTarget)?[NSString stringWithFormat:@"   needle[%@%@ curve %+.0f°]",
        _hasEntry?@"Entry":@"-", _hasTarget?@"/Target":@"", _needleAngleSlider.doubleValue]:@"";
    _info.stringValue=[NSString stringWithFormat:
    @"pos x=%ld y=%ld z=%ld   WL %.0f/WW %.0f   IVC pts:%lu   θ=%.0f°   deflection A/P=%+.0f° L/R=%+.0f°   view rot:%d°%@",
    _cx,_cy,_cz,_wl,_ww,(unsigned long)_points.count,_thetaSlider.doubleValue,_b1Slider.doubleValue,_b2Slider.doubleValue,_rot*90,nd]; }

// ---- 断面レンダリング（共通WL/WW）----
- (NSImage*)axialImg{ size_t sl=(size_t)_H*_W; float*f=_vol+(size_t)_cz*sl; unsigned char*b=malloc(sl);
    double lo=_wl-_ww/2,rg=(_ww!=0?_ww:1); for(size_t i=0;i<sl;i++){double v=(f[i]-lo)/rg;if(v<0)v=0;if(v>1)v=1;b[i]=(unsigned char)(v*255+0.5);}
    NSImage*im=grayImage(b,_W,_H);free(b);im.size=NSMakeSize(_W*_sx,_H*_sy);return im; }
- (NSImage*)coronalImg{ unsigned char*b=malloc((size_t)_W*_N); double lo=_wl-_ww/2,rg=(_ww!=0?_ww:1); size_t sl=(size_t)_H*_W;
    for(long z=0;z<_N;z++)for(long x=0;x<_W;x++){double v=(_vol[(size_t)z*sl+(size_t)_cy*_W+x]-lo)/rg;if(v<0)v=0;if(v>1)v=1;b[(_N-1-z)*_W+x]=(unsigned char)(v*255+0.5);}
    NSImage*im=grayImage(b,_W,_N);free(b);im.size=NSMakeSize(_W*_sx,_N*_dz);return im; }
- (NSImage*)sagittalImg{ unsigned char*b=malloc((size_t)_H*_N); double lo=_wl-_ww/2,rg=(_ww!=0?_ww:1); size_t sl=(size_t)_H*_W;
    for(long z=0;z<_N;z++)for(long y=0;y<_H;y++){double v=(_vol[(size_t)z*sl+(size_t)y*_W+_cx]-lo)/rg;if(v<0)v=0;if(v>1)v=1;b[(_N-1-z)*_H+y]=(unsigned char)(v*255+0.5);}
    NSImage*im=grayImage(b,_H,_N);free(b);im.size=NSMakeSize(_H*_sy,_N*_dz);return im; }

// ICE = 先端から出る平面セクター(扇)。先端2軸偏向(支点≒先端30mm近位)を反映。
- (NSImage*)iceImg{ _fanValid=NO; int K=(int)_points.count; if(K<2)return nil;
    NSArray*so=[_points sortedArrayUsingComparator:^NSComparisonResult(NSArray*a,NSArray*b){return [a[0]compare:b[0]];}];
    double pz[K],py[K],px[K]; for(int k=0;k<K;k++){pz[k]=[so[k][0]doubleValue];py[k]=[so[k][1]doubleValue];px[k]=[so[k][2]doubleValue];}
    long zmin=(long)floor(pz[0]),zmax=(long)ceil(pz[K-1]); if(zmin<0)zmin=0;if(zmax>_N-1)zmax=_N-1;if(zmax<=zmin)return nil;
    if(!_probeInit){ double z0=_tipHighZ?(double)zmax:(double)zmin;             // 初期プローブ(=先端アレイ)位置=挿入方向で決まる先端側
        _probeSlider.minValue=zmin;_probeSlider.maxValue=zmax;_probeSlider.doubleValue=z0;_probeSlider.enabled=YES;_probeInit=YES; }
    if(_probeSlider.minValue!=zmin||_probeSlider.maxValue!=zmax){_probeSlider.minValue=zmin;_probeSlider.maxValue=zmax;}
    double zP=_probeSlider.doubleValue; if(zP<zmin)zP=zmin;if(zP>zmax)zP=zmax;

    // ---- 直線ロッドICE: プローブ=まっすぐな棒(IVC経路)。アレイ＆ビームは先端の前面から(側射) ----
    double th=_thetaSlider.doubleValue*M_PI/180.0;
    double b1=_b1Slider.doubleValue*M_PI/180.0, b2=_b2Slider.doubleValue*M_PI/180.0;
    double R=85.0, FAN=45.0*M_PI/180.0;            // imaging depth 85mm / 90-degree sector
    // アレイ(ビーム射出)=まっすぐな棒の先端(=パス上のzP点)。棒は曲げない
    double Fb[3]={interp1(pz,px,K,zP)*_sx, interp1(pz,py,K,zP)*_sy, zP*_dz};
    double zA=fmin((double)zmax,zP+2), zB=fmax((double)zmin,zP-2);
    double S[3]={(interp1(pz,px,K,zA)-interp1(pz,px,K,zB))*_sx,(interp1(pz,py,K,zA)-interp1(pz,py,K,zB))*_sy,(zA-zB)*_dz}; nrm3(S);  // 棒の軸
    double Tpx=Fb[0],Tpy=Fb[1],Tpz=Fb[2];                                       // apex(アレイ)=棒上(直線)
    // ビームは先端前面から: θ=縦軸まわり安定方位、偏向はビーム向きの微調整(棒は曲げない)
    double Vp[3]={cos(th),sin(th),0.0}; nrm3(Vp);
    double axb[3]; cross3(Vp,S,axb);                                            // A/P傾け軸(⊥ビーム&棒)
    if(sqrt(axb[0]*axb[0]+axb[1]*axb[1]+axb[2]*axb[2])>1e-6){ nrm3(axb); double t[3]; rot3(Vp,axb,b1,t); Vp[0]=t[0];Vp[1]=t[1];Vp[2]=t[2]; }  // A/P: 前後に傾ける
    { double t[3]; rot3(Vp,S,b2,t); Vp[0]=t[0];Vp[1]=t[1];Vp[2]=t[2]; } nrm3(Vp);    // L/R: 棒まわりにビームを回す
    double dav=S[0]*Vp[0]+S[1]*Vp[1]+S[2]*Vp[2];
    double Sp[3]={S[0]-dav*Vp[0],S[1]-dav*Vp[1],S[2]-dav*Vp[2]};                 // 扇の展開軸=棒の軸(縦断)
    if(sqrt(Sp[0]*Sp[0]+Sp[1]*Sp[1]+Sp[2]*Sp[2])<1e-6){Sp[0]=S[0];Sp[1]=S[1];Sp[2]=S[2];} nrm3(Sp);
    // store
    _fanTip[0]=Tpx;_fanTip[1]=Tpy;_fanTip[2]=Tpz;
    _fanVp[0]=Vp[0];_fanVp[1]=Vp[1];_fanVp[2]=Vp[2];
    _fanSp[0]=Sp[0];_fanSp[1]=Sp[1];_fanSp[2]=Sp[2];
    _fanS[0]=S[0];_fanS[1]=S[1];_fanS[2]=S[2];
    _fanA[0]=S[0];_fanA[1]=S[1];_fanA[2]=S[2];                                  // アレイ軸=棒の軸(直線)
    _fanFb[0]=Fb[0];_fanFb[1]=Fb[1];_fanFb[2]=Fb[2];                            // 棒は直線→支点なし(=apex)
    _fanHalf=FAN;_fanR=R;_fanValid=YES;

    // スキャン変換: apex=上中央, 深さ=下
    double pxmm=0.6, halfW=R*sin(FAN)+12.0, depth=R+10.0;
    long Wi=(long)(2*halfW/pxmm), Hi=(long)(depth/pxmm); if(Wi<10||Hi<10)return nil;
    _iceWi=Wi;_iceHi=Hi;                                                       // 針のICE投影で再利用
    double lo=_wl-_ww/2,rg=(_ww!=0?_ww:1);
    unsigned char*b=malloc((size_t)Wi*Hi);
    for(long r=0;r<Hi;r++){ double Y=r*pxmm;
        for(long c=0;c<Wi;c++){ double X=(c-Wi/2.0)*pxmm;
            double rho=hypot(X,Y), phi=atan2(X,Y);                        // phi=0は真下(=Vp)
            double dir0=cos(phi)*Vp[0]+sin(phi)*Sp[0], dir1=cos(phi)*Vp[1]+sin(phi)*Sp[1], dir2=cos(phi)*Vp[2]+sin(phi)*Sp[2];
            double Px=Tpx+rho*dir0,Py=Tpy+rho*dir1,Pz=Tpz+rho*dir2;
            double vx=Px/_sx,vy=Py/_sy,vz=Pz/_dz;
            if(vx<0||vx>_W-1||vy<0||vy>_H-1||vz<0||vz>_N-1){ b[(size_t)r*Wi+c]=0; continue; }  // 体外=黒(バグC修正)
            float hu=vsample(_vol,_N,_H,_W,(long)lround(vz),vy,vx);
            double v=(hu-lo)/rg;if(v<0)v=0;if(v>1)v=1;
            BOOL inf=(rho<=R)&&(fabs(phi)<=FAN);
            unsigned char px8=(unsigned char)((inf?v:v*0.30)*255+0.5);
            BOOL edge=(rho>3.0)&&( (rho<=R+0.6 && fabs(fabs(phi)-FAN)<=(1.5*pxmm/(rho+1e-3))) || (fabs(rho-R)<=1.1*pxmm && fabs(phi)<=FAN) );
            if(edge) px8=240;                         // 扇の枠線を焼き込み(回転追従)
            b[(size_t)r*Wi+c]=px8;} }
    if(_iceFlip){ for(long r=0;r<Hi;r++)for(long c=0;c<Wi/2;c++){ unsigned char t=b[(size_t)r*Wi+c]; b[(size_t)r*Wi+c]=b[(size_t)r*Wi+(Wi-1-c)]; b[(size_t)r*Wi+(Wi-1-c)]=t; } }  // 左右反転
    long rw,rh; unsigned char*rb=rotateBuf(b,Wi,Hi,_rot,&rw,&rh); free(b);
    NSImage*im=grayImage(rb,rw,rh);free(rb); double pw=Wi*pxmm,ph=Hi*pxmm; im.size=(_rot%2==0)?NSMakeSize(pw,ph):NSMakeSize(ph,pw); return im; }

// ---- 向きマーカー（3断面への扇投影）----
// mm空間の点 P → 各断面の画像ピクセル座標へ（cross 定義と一致）
- (NSPoint)projMM:(const double*)P plane:(int)plane{
    double xi=P[0]/_sx, yi=P[1]/_sy, zi=P[2]/_dz;
    if(plane==0) return NSMakePoint(xi,yi);                       // Axial: (x,y)
    if(plane==1) return NSMakePoint(xi,(double)(_N-1)-zi);        // Coronal: (x, N-1-z)
    return NSMakePoint(yi,(double)(_N-1)-zi);                     // Sagittal: (y, N-1-z)
}
- (NSArray*)fanFillForPlane:(int)plane{
    if(!_fanValid)return nil;
    double Rmark=_fanR;                                           // full imaging extent (ghost of where ICE is looking)
    NSMutableArray*pts=[NSMutableArray array];
    [pts addObject:[NSValue valueWithPoint:[self projMM:_fanTip plane:plane]]];   // apex
    int M=20;
    for(int i=0;i<=M;i++){ double phi=-_fanHalf+(2.0*_fanHalf)*i/(double)M;
        double dir[3]={cos(phi)*_fanVp[0]+sin(phi)*_fanSp[0],cos(phi)*_fanVp[1]+sin(phi)*_fanSp[1],cos(phi)*_fanVp[2]+sin(phi)*_fanSp[2]};
        double Pp[3]={_fanTip[0]+Rmark*dir[0],_fanTip[1]+Rmark*dir[1],_fanTip[2]+Rmark*dir[2]};
        [pts addObject:[NSValue valueWithPoint:[self projMM:Pp plane:plane]]]; }
    return pts; }
- (NSArray*)fanBeamForPlane:(int)plane{
    if(!_fanValid)return nil;
    double Rmark=_fanR;                                           // full-length central beam (ghost)
    double Pp[3]={_fanTip[0]+Rmark*_fanVp[0],_fanTip[1]+Rmark*_fanVp[1],_fanTip[2]+Rmark*_fanVp[2]};
    return @[[NSValue valueWithPoint:[self projMM:_fanTip plane:plane]],[NSValue valueWithPoint:[self projMM:Pp plane:plane]]]; }

- (void)refreshAll{
    _pAx.imgW=_W;_pAx.imgH=_H;_pAx.img=[self axialImg]; _pAx.cross=NSMakePoint(_cx,_cy);_pAx.showCross=YES;
    NSMutableArray*m=[NSMutableArray array]; for(NSArray*pt in _points){long pz=[pt[0]longValue]; if(labs(pz-_cz)<=1)[m addObject:[NSValue valueWithPoint:NSMakePoint([pt[2]doubleValue],[pt[1]doubleValue])]];}
    _pAx.markers=m;
    _pCor.imgW=_W;_pCor.imgH=_N;_pCor.img=[self coronalImg]; _pCor.cross=NSMakePoint(_cx,_N-1-_cz);_pCor.showCross=YES;
    _pSag.imgW=_H;_pSag.imgH=_N;_pSag.img=[self sagittalImg]; _pSag.cross=NSMakePoint(_cy,_N-1-_cz);_pSag.showCross=YES;
    _pIce.img=[self iceImg];                                       // ← _fanValid と幾何を更新
    _pAx.fanFill =[self fanFillForPlane:0]; _pAx.fanBeam =[self fanBeamForPlane:0];
    _pCor.fanFill=[self fanFillForPlane:1]; _pCor.fanBeam=[self fanBeamForPlane:1];
    _pSag.fanFill=[self fanFillForPlane:2]; _pSag.fanBeam=[self fanBeamForPlane:2];
    if(_fanValid){ _pIce.imgW=(_rot%2==0)?_iceWi:_iceHi; _pIce.imgH=(_rot%2==0)?_iceHi:_iceWi; }  // ICEペイン画素寸法(針投影用)
    [self updatePathOverlays];                                                            // IVCパス中心線を3断面へ
    [self updateNeedleOverlays];                                                          // 針軌道を4面へ
    [self update3D];                                                                      // 連動3Dビュー
    _zSlider.integerValue=_cz; _ySlider.integerValue=_cy; _xSlider.integerValue=_cx;   // スライダー同期
    for(TIPSPaneView*p in @[_pAx,_pCor,_pSag,_pIce])p.needsDisplay=YES; [self updateInfo];
}

// ---- 操作 ----
- (void)adjustWL:(double)dWL ww:(double)dWW{ _ww+=dWW*2.0; _wl+=dWL*2.0; if(_ww<1)_ww=1; [self refreshAll]; }
- (void)navChanged:(id)sender{ _cz=_zSlider.integerValue; _cy=_ySlider.integerValue; _cx=_xSlider.integerValue; [self refreshAll]; }
- (void)scrollPlane:(int)pl by:(double)s{ long step=(s>0?1:-1);
    if(pl==0){_cz+=step; if(_cz<0)_cz=0;if(_cz>_N-1)_cz=_N-1;}
    else if(pl==1){_cy+=step; if(_cy<0)_cy=0;if(_cy>_H-1)_cy=_H-1;}
    else {_cx+=step; if(_cx<0)_cx=0;if(_cx>_W-1)_cx=_W-1;}
    [self refreshAll]; }
// トグルボタンの選択状態＋ステップ表示切替をモデルに同期
- (void)updateModeButtons{
    BOOL s1=(_step==0);
    #define ON NSControlStateValueOn
    #define OFF NSControlStateValueOff
    _step1Btn.state=s1?ON:OFF; _step2Btn.state=s1?OFF:ON;
    // Step1のみ表示: 挿入方向＋ヒント
    _step1Hint.hidden=!s1; _step1Hint2.hidden=!s1; _femBtn.hidden=!s1; _jugBtn.hidden=!s1;
    _femBtn.state=_tipHighZ?ON:OFF; _jugBtn.state=_tipHighZ?OFF:ON;
    // Step2のみ表示: Entry/Target/針種/Curve
    _entryBtn.hidden=s1; _targetBtn.hidden=s1;
    _curveLbl.hidden=s1; _needleAngleSlider.hidden=s1; _bendVal.hidden=s1; _clrNBtn.hidden=s1;
    _entryBtn.state =(_ptMode==0)?ON:OFF; _targetBtn.state=(_ptMode==1)?ON:OFF;
    #undef ON
    #undef OFF
}
- (void)goStep1:(id)s{ _step=0; [self updateModeButtons]; [self refreshAll]; }
- (void)goStep2:(id)s{ _step=1; if(!_hasEntry&&!_hasTarget)_ptMode=0; [self updateModeButtons]; [self refreshAll]; }
- (void)useFemoral:(id)s{ _tipHighZ=YES; _probeInit=NO; [self jumpToTipSlice]; [self updateModeButtons]; [self refreshAll]; }   // 足から=先端は頭(高z)側
- (void)useJugular:(id)s{ _tipHighZ=NO;  _probeInit=NO; [self jumpToTipSlice]; [self updateModeButtons]; [self refreshAll]; }   // 頸から=先端は足(低z)側
// Axialを先端(TIP)スライスへ移動（投影スライス不一致による見かけのズレを解消）
- (void)jumpToTipSlice{
    if(_points.count<2)return;
    double zmn=1e9,zmx=-1e9; for(NSArray*pt in _points){double z=[pt[0]doubleValue]; if(z<zmn)zmn=z; if(z>zmx)zmx=z;}
    _cz=(long)lround(_tipHighZ?zmx:zmn); if(_cz<0)_cz=0; if(_cz>_N-1)_cz=_N-1; }
- (void)probeChanged:(id)s{ _cz=(long)lround(_probeSlider.doubleValue); if(_cz<0)_cz=0; if(_cz>_N-1)_cz=_N-1; [self refreshAll]; }  // プローブ位置にAxial追従
- (void)setEntryMode:(id)s{ _step=1; _ptMode=0; [self updateModeButtons]; }
- (void)setTargetMode:(id)s{ _step=1; _ptMode=1; [self updateModeButtons]; }
// Entry/Target を mm でセットし、Entry→Target に自動進行
- (void)setNeedlePointMM:(double)x y:(double)y z:(double)z{
    if(_ptMode==0){ _entry[0]=x;_entry[1]=y;_entry[2]=z; _hasEntry=YES; _ptMode=1; }   // Entry set → 次はTarget
    else { _target[0]=x;_target[1]=y;_target[2]=z; _hasTarget=YES; }                     // Target set（留まる＝再クリックで微調整）
    [self updateModeButtons];
}
- (void)axialClick:(double)ix y:(double)iy{ _cx=(long)lround(ix);_cy=(long)lround(iy);
    if(_step==0){ [_points addObject:@[@(_cz),@(iy),@(ix)]]; if(_points.count==2){_probeInit=NO;[self jumpToTipSlice];} }  // Step1: 2点で先端スライスへ
    else { [self setNeedlePointMM:ix*_sx y:iy*_sy z:_cz*_dz]; }                                       // Step2: Entry/Target
    [self refreshAll]; }
// ICEクリック → ICE平面上の3D点を Entry/Target に（CT断面に針ラインが再現される）。Step2のみ
- (void)iceClick:(double)ix y:(double)iy{
    if(!_fanValid||_iceWi<=0||_step!=1)return;
    long Wd=(_rot%2==0)?_iceWi:_iceHi, Hd=(_rot%2==0)?_iceHi:_iceWi;
    NSPoint p=invRotPixel(NSMakePoint(ix,iy),&Wd,&Hd,_rot);                                           // 表示画素→回転前(Wi,Hi)
    if(_iceFlip)p.x=(double)(_iceWi-1)-p.x;                                                            // 左右反転を戻す
    double depth=p.y*0.6, lat=(p.x-_iceWi/2.0)*0.6;
    double Q[3]={_fanTip[0]+depth*_fanVp[0]+lat*_fanSp[0], _fanTip[1]+depth*_fanVp[1]+lat*_fanSp[1], _fanTip[2]+depth*_fanVp[2]+lat*_fanSp[2]};
    [self setNeedlePointMM:Q[0] y:Q[1] z:Q[2]];
    _cx=(long)lround(Q[0]/_sx); _cy=(long)lround(Q[1]/_sy); _cz=(long)lround(Q[2]/_dz);              // その点のスライスへ
    if(_cx<0)_cx=0;if(_cx>_W-1)_cx=_W-1; if(_cy<0)_cy=0;if(_cy>_H-1)_cy=_H-1; if(_cz<0)_cz=0;if(_cz>_N-1)_cz=_N-1;
    [self refreshAll]; }
- (void)clearNeedle:(id)s{ _hasEntry=NO; _hasTarget=NO; [self refreshAll]; }
// Entry(which=1)/Target(which=2)の点を、各断面の面内でドラッグ移動（面外座標は保持）。ICE=ICE平面上の3D点へ
- (void)movePoint:(int)which to:(double)ix y:(double)iy plane:(int)plane{
    double*P=(which==1)?_entry:_target;
    if(plane==0){ P[0]=ix*_sx; P[1]=iy*_sy; }                                  // Axial: x,y（zは保持）
    else if(plane==1){ P[0]=ix*_sx; P[2]=((double)(_N-1)-iy)*_dz; }            // Coronal: x,z（yは保持）
    else if(plane==2){ P[1]=ix*_sy; P[2]=((double)(_N-1)-iy)*_dz; }            // Sagittal: y,z（xは保持）
    else { if(!_fanValid||_iceWi<=0)return;                                    // ICE: ICE平面上の3D点
        long Wd=(_rot%2==0)?_iceWi:_iceHi, Hd=(_rot%2==0)?_iceHi:_iceWi;
        NSPoint p=invRotPixel(NSMakePoint(ix,iy),&Wd,&Hd,_rot); if(_iceFlip)p.x=(double)(_iceWi-1)-p.x;
        double depth=p.y*0.6, lat=(p.x-_iceWi/2.0)*0.6;
        P[0]=_fanTip[0]+depth*_fanVp[0]+lat*_fanSp[0]; P[1]=_fanTip[1]+depth*_fanVp[1]+lat*_fanSp[1]; P[2]=_fanTip[2]+depth*_fanVp[2]+lat*_fanSp[2]; }
    if(which==1)_hasEntry=YES; else _hasTarget=YES;
    [self refreshAll]; }
// Entry→Target の針軌道(mm)。針はタイプ無し＝Curve角度のみ：0°=直線、正負でEntry・Targetを必ず結ぶ円弧の弓なり方向。Curve(launch角)で湾曲量を可変
- (int)needlePath:(double(*)[3])out max:(int)maxn{
    if(!_hasEntry||!_hasTarget)return 0;
    double P[3]={_entry[0],_entry[1],_entry[2]};
    double C[3]={_target[0]-P[0],_target[1]-P[1],_target[2]-P[2]};
    double d=sqrt(C[0]*C[0]+C[1]*C[1]+C[2]*C[2]); if(d<1.0)return 0;
    int n=44; if(n>maxn)n=maxn;
    double beta=_needleAngleSlider.doubleValue*M_PI/180.0;
    if(fabs(beta)<0.02){ for(int i=0;i<n;i++){double t=(double)i/(n-1); out[i][0]=P[0]+C[0]*t;out[i][1]=P[1]+C[1]*t;out[i][2]=P[2]+C[2]*t;} return n; }
    double u[3]={C[0]/d,C[1]/d,C[2]/d}, zhat[3]={0,0,1}, bb[3]; cross3(u,zhat,bb);
    if(sqrt(bb[0]*bb[0]+bb[1]*bb[1]+bb[2]*bb[2])<1e-6){double yh[3]={0,1,0};cross3(u,yh,bb);} nrm3(bb);
    double Cx=d/2.0, Cy=-d/(2.0*tan(beta));                              // 弧中心(u,bb): Entry/Target両方を通る
    double R=sqrt(Cx*Cx+Cy*Cy);
    double aP=atan2(0-Cy,0-Cx), aT=atan2(0-Cy,d-Cx);
    double dang=aT-aP; while(dang>M_PI)dang-=2*M_PI; while(dang<-M_PI)dang+=2*M_PI;
    for(int i=0;i<n;i++){ double ang=aP+dang*(double)i/(n-1);
        double pu=Cx+R*cos(ang), pb=Cy+R*sin(ang);
        out[i][0]=P[0]+pu*u[0]+pb*bb[0]; out[i][1]=P[1]+pu*u[1]+pb*bb[1]; out[i][2]=P[2]+pu*u[2]+pb*bb[2]; }
    return n; }
// mm点 Q → ICE表示画像の画素(_rot反映)。深さが手前(>=0想定)でなくても投影は返す
- (BOOL)iceProj:(const double*)Q out:(NSPoint*)outP{
    if(!_fanValid||_iceWi<=0)return NO;
    double w[3]={Q[0]-_fanTip[0],Q[1]-_fanTip[1],Q[2]-_fanTip[2]};
    double depth=w[0]*_fanVp[0]+w[1]*_fanVp[1]+w[2]*_fanVp[2];
    double lat=w[0]*_fanSp[0]+w[1]*_fanSp[1]+w[2]*_fanSp[2];
    double pxmm=0.6; NSPoint p=NSMakePoint(_iceWi/2.0+lat/pxmm, depth/pxmm);
    if(_iceFlip)p.x=(double)(_iceWi-1)-p.x;                                    // 画像の左右反転に合わせて投影も反転
    long W=_iceWi,H=_iceHi; *outP=rotPixel(p,&W,&H,_rot); return YES; }
// 連動3Dビューへ幾何を送る（ICEと同一幾何で駆動）
- (void)update3D{
    if(!_p3d)return;
    if(!_fanValid){ _p3d.valid=NO; [_p3d setNeedsDisplay:YES]; return; }
    // ---- 偏向で先端がぐぐっと曲がるモデル ----
    // 屈曲部(bending section, 先端〜30mm近位)を支点に、遠位ポリラインを「F側=0 → 先端=β」で進行性に回す。
    // β=0(偏向なし)なら経路そのまま＝灰と滑らかに連続。ICEの2D像は別計算(_fanTip)のため不変。
    P3 apOn={_fanTip[0],_fanTip[1],_fanTip[2]};                                     // on-path apex (ICE 2Dと共有)
    double dd=_tipHighZ?1.0:-1.0;                                                   // 遠位(先端)＝高z/低z
    double td3[3]={dd*_fanS[0],dd*_fanS[1],dd*_fanS[2]}; nrm3(td3);                 // 遠位向き単位
    double Lb=30.0;                                                                 // 屈曲部長 mm
    double b1r=_b1Slider.doubleValue*M_PI/180.0, b2r=_b2Slider.doubleValue*M_PI/180.0;
    int K=(int)_points.count; NSMutableArray*gray=[NSMutableArray array],*orange=[NSMutableArray array];
    P3 Tp=apOn;                                                                     // 偏向後の先端(既定=apOn)
    if(K>=2){ NSArray*so=[_points sortedArrayUsingComparator:^NSComparisonResult(NSArray*a,NSArray*b){return [a[0]compare:b[0]];}];
        double pz[K],py[K],px[K]; for(int k=0;k<K;k++){pz[k]=[so[k][0]doubleValue];py[k]=[so[k][1]doubleValue];px[k]=[so[k][2]doubleValue];}
        long z0=(long)floor(pz[0]),z1=(long)ceil(pz[K-1]);
        double zP=_probeSlider.doubleValue; if(zP<z0)zP=z0; if(zP>z1)zP=z1;
        // 1) 遠位ポリライン(先端apOn → 近位, 累積3D長 Lb まで)。index0=apex … 末尾=支点F
        NSMutableArray*distal=[NSMutableArray array]; [distal addObject:boxP3(apOn)];
        double acc=0, prx=apOn.x,pry=apOn.y,prz=apOn.z, zf=zP;
        for(double zz=zP-dd; (dd>0? zz>=z0 : zz<=z1); zz-=dd){
            P3 p={interp1(pz,px,K,zz)*_sx, interp1(pz,py,K,zz)*_sy, zz*_dz};
            acc+=sqrt((p.x-prx)*(p.x-prx)+(p.y-pry)*(p.y-pry)+(p.z-prz)*(p.z-prz)); prx=p.x;pry=p.y;prz=p.z;
            [distal addObject:boxP3(p)]; zf=zz; if(acc>=Lb)break; }
        int md=(int)distal.count; P3 F=unboxP3(distal[md-1]);                       // 支点(≈Lb proximal)
        // 2) 灰シャフト＝F より近位の経路(粗ステップ)＋支点Fで接続
        for(double zz=zf-dd; (dd>0? zz>=z0 : zz<=z1); zz-=2.0*dd){
            P3 p={interp1(pz,px,K,zz)*_sx, interp1(pz,py,K,zz)*_sy, zz*_dz}; [gray insertObject:boxP3(p) atIndex:0]; }
        [gray addObject:boxP3(F)];
        // 3) 支点での接線 t0(遠位向き) と 曲げ方向 n(A/P=アレイ正面Vp0, L/R=直交)
        P3 q1=unboxP3(distal[md>=2?md-2:0]); double t0[3]={q1.x-F.x,q1.y-F.y,q1.z-F.z};
        if(sqrt(t0[0]*t0[0]+t0[1]*t0[1]+t0[2]*t0[2])<1e-6){t0[0]=td3[0];t0[1]=td3[1];t0[2]=td3[2];} nrm3(t0);
        double th=_thetaSlider.doubleValue*M_PI/180.0; double Vp0[3]={cos(th),sin(th),0.0};
        double dot0=Vp0[0]*t0[0]+Vp0[1]*t0[1]+Vp0[2]*t0[2];
        double nAP[3]={Vp0[0]-dot0*t0[0],Vp0[1]-dot0*t0[1],Vp0[2]-dot0*t0[2]};
        if(sqrt(nAP[0]*nAP[0]+nAP[1]*nAP[1]+nAP[2]*nAP[2])<1e-6){nAP[0]=1;nAP[1]=0;nAP[2]=0;} nrm3(nAP);
        double nLR[3]; cross3(t0,nAP,nLR); nrm3(nLR);
        double n[3]={b1r*nAP[0]+b2r*nLR[0], b1r*nAP[1]+b2r*nLR[1], b1r*nAP[2]+b2r*nLR[2]};
        double beta=sqrt(b1r*b1r+b2r*b2r), nlen=sqrt(n[0]*n[0]+n[1]*n[1]+n[2]*n[2]);
        if(md>=2 && beta>1e-4 && nlen>1e-9){ nrm3(n);                               // 定曲率円弧: 起点F・接線t0・n方向へ全角β（先端接線=ちょうどβ, ループ化しない）。md<2(遠位点なし)はファントム弧を出さず直線扱い
            double tot=(acc>1e-6?acc:Lb), rho=tot/beta; int NB=(md>2?md-1:10);
            for(int i=0;i<=NB;i++){ double s=beta*i/(double)NB, cs=rho*sin(s), cn=rho*(1.0-cos(s));
                [orange addObject:boxP3((P3){F.x+cs*t0[0]+cn*n[0], F.y+cs*t0[1]+cn*n[1], F.z+cs*t0[2]+cn*n[2]})]; }
            Tp=unboxP3(orange[orange.count-1]);
        } else { for(int i=md-1;i>=0;i--)[orange addObject:distal[i]]; Tp=apOn; }   // 偏向なし=経路そのまま
    }
    _p3d.pathPts=gray; _p3d.tipSeg=orange;                                          // apexPt(=射出点)はアレイ中央amidで後段設定
    // 前面アレイパッチ(青): 曲がった先端Tpから先端軸に沿って近位へ。Vp側へオフセット
    P3 tipPrev=(orange.count>=2)?unboxP3(orange[orange.count-2]):apOn;
    double t1[3]={Tp.x-tipPrev.x,Tp.y-tipPrev.y,Tp.z-tipPrev.z};
    if(sqrt(t1[0]*t1[0]+t1[1]*t1[1]+t1[2]*t1[2])<1e-6){t1[0]=td3[0];t1[1]=td3[1];t1[2]=td3[2];} nrm3(t1);
    double rr=2.5; P3 a1={Tp.x+rr*_fanVp[0],Tp.y+rr*_fanVp[1],Tp.z+rr*_fanVp[2]};
    P3 a0={Tp.x-12*t1[0]+rr*_fanVp[0], Tp.y-12*t1[1]+rr*_fanVp[1], Tp.z-12*t1[2]+rr*_fanVp[2]};
    _p3d.arraySeg=@[boxP3(a0),boxP3(a1)];                                           // 青=先端前面のアレイパッチ
    P3 amid={(a0.x+a1.x)/2.0,(a0.y+a1.y)/2.0,(a0.z+a1.z)/2.0};                      // ビーム/扇の射出点=アレイ中央(側射)。先端(頂上)ではなく中央から出す
    _p3d.apexPt=boxP3(amid);
    double dvt=t1[0]*_fanVp[0]+t1[1]*_fanVp[1]+t1[2]*_fanVp[2];                      // 扇の展開軸=曲がった先端軸の⊥ビーム成分（扇が橙先端に張り付く）。ビーム方向は2D ICEと同じ_fanVp
    double Sp3[3]={t1[0]-dvt*_fanVp[0], t1[1]-dvt*_fanVp[1], t1[2]-dvt*_fanVp[2]};
    if(sqrt(Sp3[0]*Sp3[0]+Sp3[1]*Sp3[1]+Sp3[2]*Sp3[2])<1e-6){Sp3[0]=_fanSp[0];Sp3[1]=_fanSp[1];Sp3[2]=_fanSp[2];} nrm3(Sp3);
    NSMutableArray*fan=[NSMutableArray array]; [fan addObject:boxP3(amid)];
    int M=24; for(int i=0;i<=M;i++){ double phi=-_fanHalf+2.0*_fanHalf*i/(double)M;
        P3 q={amid.x+_fanR*(cos(phi)*_fanVp[0]+sin(phi)*Sp3[0]), amid.y+_fanR*(cos(phi)*_fanVp[1]+sin(phi)*Sp3[1]), amid.z+_fanR*(cos(phi)*_fanVp[2]+sin(phi)*Sp3[2])};
        [fan addObject:boxP3(q)]; }
    _p3d.fanPoly=fan;
    _p3d.beamEnd=boxP3((P3){amid.x+_fanR*_fanVp[0],amid.y+_fanR*_fanVp[1],amid.z+_fanR*_fanVp[2]});
    double NP[64][3]; int nn=[self needlePath:NP max:64]; NSMutableArray*nd=[NSMutableArray array];
    for(int i=0;i<nn;i++){ P3 p={NP[i][0],NP[i][1],NP[i][2]}; [nd addObject:boxP3(p)]; }
    _p3d.needlePts=(nn>1?nd:nil);
    _p3d.entryPt =_hasEntry ?boxP3((P3){_entry[0],_entry[1],_entry[2]}):nil;
    _p3d.targetPt=_hasTarget?boxP3((P3){_target[0],_target[1],_target[2]}):nil;
    _p3d.theta=_thetaSlider.doubleValue; _p3d.b1=_b1Slider.doubleValue; _p3d.b2=_b2Slider.doubleValue;   // 手元④軸ダイヤル値
    _p3d.probeFrac=(_probeSlider.maxValue>_probeSlider.minValue)?(_probeSlider.doubleValue-_probeSlider.minValue)/(_probeSlider.maxValue-_probeSlider.minValue):0.5;
    _p3d.valid=YES; [_p3d setNeedsDisplay:YES];
}
// IVCパス中心線を補間して3断面に投影（プローブ点がパス上に在ることが見えるように）
- (void)updatePathOverlays{
    TIPSPaneView*op[3]={_pAx,_pCor,_pSag};
    int K=(int)_points.count;
    if(K<2){ for(int p=0;p<3;p++){op[p].pathPts=nil;op[p].tipPt=nil;} return; }
    NSArray*so=[_points sortedArrayUsingComparator:^NSComparisonResult(NSArray*a,NSArray*b){return [a[0]compare:b[0]];}];
    double pz[K],py[K],px[K]; for(int k=0;k<K;k++){pz[k]=[so[k][0]doubleValue];py[k]=[so[k][1]doubleValue];px[k]=[so[k][2]doubleValue];}
    long z0=(long)floor(pz[0]),z1=(long)ceil(pz[K-1]);
    int ti=_tipHighZ?(K-1):0; double tipmm[3]={px[ti]*_sx,py[ti]*_sy,pz[ti]*_dz};   // 先端=挿入方向で決まる端
    for(int pl=0;pl<3;pl++){ NSMutableArray*a=[NSMutableArray array];
        for(long z=z0;z<=z1;z+=2){ double mm[3]={interp1(pz,px,K,(double)z)*_sx, interp1(pz,py,K,(double)z)*_sy, (double)z*_dz};
            [a addObject:[NSValue valueWithPoint:[self projMM:mm plane:pl]]]; }
        op[pl].pathPts=a; op[pl].tipPt=[NSValue valueWithPoint:[self projMM:tipmm plane:pl]]; }
}
- (void)updateNeedleOverlays{
    TIPSPaneView*panes[4]={_pAx,_pCor,_pSag,_pIce};
    double NP[64][3]; int nn=[self needlePath:NP max:64];
    for(int pl=0;pl<4;pl++){ TIPSPaneView*pane=panes[pl];
        if(!_hasEntry&&!_hasTarget){ pane.needlePts=nil;pane.needleCross=nil;pane.entryPt=nil;pane.targetPt=nil;pane.entryBig=NO;pane.targetBig=NO; continue; }
        // 現在の断面/平面が真のEntry/Target を含むか → 拡大表示フラグ
        BOOL eb=NO,tb=NO;
        if(pl==0){ eb=_hasEntry&&labs(_cz-(long)lround(_entry[2]/_dz))<=1; tb=_hasTarget&&labs(_cz-(long)lround(_target[2]/_dz))<=1; }
        else if(pl==1){ eb=_hasEntry&&labs(_cy-(long)lround(_entry[1]/_sy))<=1; tb=_hasTarget&&labs(_cy-(long)lround(_target[1]/_sy))<=1; }
        else if(pl==2){ eb=_hasEntry&&labs(_cx-(long)lround(_entry[0]/_sx))<=1; tb=_hasTarget&&labs(_cx-(long)lround(_target[0]/_sx))<=1; }
        else if(_fanValid){ double n0[3];cross3(_fanVp,_fanSp,n0);
            if(_hasEntry){double w[3]={_entry[0]-_fanTip[0],_entry[1]-_fanTip[1],_entry[2]-_fanTip[2]}; eb=fabs(w[0]*n0[0]+w[1]*n0[1]+w[2]*n0[2])<=3.0;}
            if(_hasTarget){double w[3]={_target[0]-_fanTip[0],_target[1]-_fanTip[1],_target[2]-_fanTip[2]}; tb=fabs(w[0]*n0[0]+w[1]*n0[1]+w[2]*n0[2])<=3.0;} }
        pane.entryBig=eb; pane.targetBig=tb;
        if(pl<3){
            if(nn>1){ NSMutableArray*pts=[NSMutableArray array]; for(int i=0;i<nn;i++)[pts addObject:[NSValue valueWithPoint:[self projMM:NP[i] plane:pl]]]; pane.needlePts=pts; } else pane.needlePts=nil;
            pane.needleCross=nil;
            pane.entryPt =_hasEntry ?[NSValue valueWithPoint:[self projMM:_entry  plane:pl]]:nil;
            pane.targetPt=_hasTarget?[NSValue valueWithPoint:[self projMM:_target plane:pl]]:nil;
        } else {
            NSMutableArray*pts=[NSMutableArray array],*cross=[NSMutableArray array]; NSPoint q;
            if(nn>1&&_fanValid){ double n0[3]; cross3(_fanVp,_fanSp,n0); double prevo=0; BOOL hp=NO;
                for(int i=0;i<nn;i++){ if([self iceProj:NP[i] out:&q])[pts addObject:[NSValue valueWithPoint:q]];
                    double w[3]={NP[i][0]-_fanTip[0],NP[i][1]-_fanTip[1],NP[i][2]-_fanTip[2]}; double o=w[0]*n0[0]+w[1]*n0[1]+w[2]*n0[2];
                    if(hp&&prevo*o<0){ double t=prevo/(prevo-o); double Qc[3]={NP[i-1][0]+(NP[i][0]-NP[i-1][0])*t,NP[i-1][1]+(NP[i][1]-NP[i-1][1])*t,NP[i-1][2]+(NP[i][2]-NP[i-1][2])*t}; NSPoint cq; if([self iceProj:Qc out:&cq])[cross addObject:[NSValue valueWithPoint:cq]]; }
                    prevo=o; hp=YES; } }
            pane.needlePts=(pts.count>1?pts:nil); pane.needleCross=cross;
            if(_hasEntry&&_fanValid&&[self iceProj:_entry out:&q])pane.entryPt=[NSValue valueWithPoint:q]; else pane.entryPt=nil;
            if(_hasTarget&&_fanValid&&[self iceProj:_target out:&q])pane.targetPt=[NSValue valueWithPoint:q]; else pane.targetPt=nil;
        }
    }
}
- (void)coronalClick:(double)ix y:(double)iy{ _cx=(long)lround(ix); _cz=_N-1-(long)lround(iy); if(_cz<0)_cz=0;if(_cz>_N-1)_cz=_N-1; [self refreshAll]; }
- (void)sagittalClick:(double)ix y:(double)iy{ _cy=(long)lround(ix); _cz=_N-1-(long)lround(iy); if(_cz<0)_cz=0;if(_cz>_N-1)_cz=_N-1; [self refreshAll]; }
- (void)ctrlChanged:(id)s{ [self refreshAll]; }
- (void)rotate90:(id)s{ _rot=(_rot+1)%4; [self refreshAll]; }
- (void)flipICE:(id)s{ _iceFlip=!_iceFlip; [self refreshAll]; }   // ICE左右反転
- (void)rotateThetaBy:(double)s{ double v=_thetaSlider.doubleValue+(s>0?5.0:-5.0); while(v<0)v+=360;while(v>=360)v-=360; _thetaSlider.doubleValue=v; [self refreshAll]; }  // ICEホイール=θ回転(5°/ノッチ)
- (void)centerDeflect:(id)s{ _b1Slider.doubleValue=0; _b2Slider.doubleValue=0; [self refreshAll]; }
- (void)clearPath:(id)s{ [_points removeAllObjects]; _probeInit=NO; _probeSlider.enabled=NO; _probeSlider.minValue=0; _probeSlider.maxValue=1; _probeSlider.doubleValue=0.5; [self refreshAll]; }
- (void)windowWillClose:(NSNotification*)n{ if(_vol){free(_vol);_vol=NULL;}
    // 閉じたら保持配列から外す（self を捕捉して安全に遅延解放：ウィンドウ蓄積リークを防止）
    dispatch_async(dispatch_get_main_queue(),^{ [gTIPSControllers removeObject:self]; }); }
@end

// ===== プラグイン本体 =====
@interface TIPSPlannerFilter : PluginFilter
@end
static float* TIPSBuildVolume(NSArray*pixList,long*oN,long*oH,long*oW,double*oDz,double*oSx,double*oSy){
    NSArray*so=[pixList sortedArrayUsingComparator:^NSComparisonResult(DCMPix*a,DCMPix*b){double za=[a sliceLocation],zb=[b sliceLocation];return(za<zb)?NSOrderedAscending:(za>zb?NSOrderedDescending:NSOrderedSame);}];
    long N=so.count;if(N==0)return NULL;DCMPix*p0=so[0];long H=[p0 pheight],W=[p0 pwidth];if(H<=0||W<=0)return NULL;
    double sx=[p0 pixelSpacingX],sy=[p0 pixelSpacingY];if(sx<=0)sx=1;if(sy<=0)sy=1;
    double dz=0;if(N>1)dz=fabs([(DCMPix*)so.lastObject sliceLocation]-[p0 sliceLocation])/(N-1);if(dz<=0)dz=([p0 sliceThickness]>0?[p0 sliceThickness]:1);
    size_t sl=(size_t)H*W;float*vol=(float*)malloc((size_t)N*sl*sizeof(float));if(!vol)return NULL;
    for(long i=0;i<N;i++){DCMPix*p=so[i];float*f=[p fImage];if(f&&[p pwidth]==W&&[p pheight]==H)memcpy(vol+(size_t)i*sl,f,sl*sizeof(float));else memset(vol+(size_t)i*sl,0,sl*sizeof(float));}
    *oN=N;*oH=H;*oW=W;*oDz=dz;*oSx=sx;*oSy=sy;return vol; }
@implementation TIPSPlannerFilter
- (long)filterImage:(NSString*)menuName{
    ViewerController*vc=viewerController;if(!vc)vc=[ViewerController frontMostDisplayed2DViewer];
    NSMutableArray*pix=vc?[vc pixList]:nil;
    if(pix.count==0){NSAlert*a=[[NSAlert alloc]init];a.messageText=@"TIPS Planner";a.informativeText=@"Open a CT series first.";[a addButtonWithTitle:@"OK"];[a runModal];return 0;}
    if(!gTIPSDisclaimerShown){ gTIPSDisclaimerShown=YES;
        NSAlert*a=[[NSAlert alloc]init]; a.messageText=@"TIPS Planner — research / education tool";
        a.informativeText=@"This is a prototype tool for research, education and self-training only.\n\n· Not a certified medical device.\n· Not intended to diagnose, treat or prevent disease.\n· Not intraprocedural navigation.\n· The operator makes all final clinical decisions.\n\n— — — — —\nMade by M. Yamamoto (independent developer). Development takes time and money and is self-funded — if this tool is useful to you, a donation to help continue development is gratefully appreciated (Ko-fi / Buy Me a Coffee). A donation supports open-source development; it is not the sale of a medical device and carries no warranty.";
        [a addButtonWithTitle:@"I understand"]; [a runModal]; }
    long N=0,H=0,W=0;double dz=1,sx=1,sy=1;float*vol=TIPSBuildVolume(pix,&N,&H,&W,&dz,&sx,&sy);
    if(!vol){NSAlert*a=[[NSAlert alloc]init];a.messageText=@"TIPS Planner";a.informativeText=@"Failed to build the volume.";[a addButtonWithTitle:@"OK"];[a runModal];return 0;}
    if(!gTIPSControllers)gTIPSControllers=[NSMutableArray array];
    [gTIPSControllers addObject:[[TIPSPlannerController alloc]initWithVol:vol N:N H:H W:W dz:dz sx:sx sy:sy]];
    return 0; }
@end
