/* Independent Xlib fixture: pixel colors and event log expose actual input. */
#include <X11/Xlib.h>
#include <X11/keysym.h>
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    Display *d = XOpenDisplay(NULL);
    if (!d) { fputs("no display\n", stderr); return 2; }
    Window w = XCreateSimpleWindow(d, DefaultRootWindow(d), 20, 30, 320, 240,
                                    0, 0, 0x183048);
    XStoreName(d, w, "ISD fixture");
    XSelectInput(d, w, ExposureMask | KeyPressMask | KeyReleaseMask |
                       ButtonPressMask | ButtonReleaseMask | PointerMotionMask);
    XMapWindow(d, w);
    GC gc = XCreateGC(d, w, 0, NULL);
    unsigned long color = 0x183048;
    setvbuf(stdout, NULL, _IOLBF, 0);
    printf("window=%lu display=%s\n", w, DisplayString(d));
    while (1) {
        XEvent e;
        XNextEvent(d, &e);
        switch (e.type) {
        case KeyPress:
            color = 0x2ca05a;
            printf("key-down=%lu\n", XLookupKeysym(&e.xkey, 0));
            break;
        case KeyRelease:
            printf("key-up=%lu\n", XLookupKeysym(&e.xkey, 0));
            break;
        case ButtonPress:
            color = 0xc04c38;
            printf("mouse-down=%u at=%d,%d\n", e.xbutton.button, e.xbutton.x, e.xbutton.y);
            break;
        case ButtonRelease:
            printf("mouse-up=%u\n", e.xbutton.button);
            break;
        case MotionNotify:
            printf("motion=%d,%d\n", e.xmotion.x, e.xmotion.y);
            break;
        }
        XSetForeground(d, gc, color);
        XFillRectangle(d, w, gc, 0, 0, 320, 240);
        XSetForeground(d, gc, 0xffffff);
        XFillRectangle(d, w, gc, 40, 40, 100, 60);
        XDrawString(d, w, gc, 20, 180, "ISD real X11 pixels", 19);
        XFlush(d);
    }
}
