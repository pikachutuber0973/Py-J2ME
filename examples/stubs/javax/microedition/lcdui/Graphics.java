package javax.microedition.lcdui;
public class Graphics {
    public static final int HCENTER=1, VCENTER=2, LEFT=4, RIGHT=8, TOP=16, BOTTOM=32, BASELINE=64, SOLID=0, DOTTED=1;
    public void setColor(int r, int g, int b) {}
    public void setColor(int rgb) {}
    public int getColor() { return 0; }
    public void drawLine(int x1, int y1, int x2, int y2) {}
    public void drawRect(int x, int y, int w, int h) {}
    public void fillRect(int x, int y, int w, int h) {}
    public void drawString(String s, int x, int y, int anchor) {}
    public void drawImage(Image img, int x, int y, int anchor) {}
    public void setFont(Font f) {}
    public void translate(int x, int y) {}
    public void setClip(int x, int y, int w, int h) {}
}
