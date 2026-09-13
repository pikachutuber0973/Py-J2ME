package javax.microedition.lcdui;
public abstract class Canvas extends Displayable {
    public static final int UP=1, LEFT=2, RIGHT=5, DOWN=6, FIRE=8, GAME_A=9, GAME_B=10, GAME_C=11, GAME_D=12;
    public static final int KEY_NUM0=48, KEY_NUM1=49, KEY_NUM2=50, KEY_NUM3=51, KEY_NUM4=52,
            KEY_NUM5=53, KEY_NUM6=54, KEY_NUM7=55, KEY_NUM8=56, KEY_NUM9=57, KEY_STAR=42, KEY_POUND=35;
    protected abstract void paint(Graphics g);
    public int getWidth() { return 0; }
    public int getHeight() { return 0; }
    public void repaint() {}
    public void repaint(int x, int y, int w, int h) {}
    public void serviceRepaints() {}
    public int getGameAction(int keyCode) { return 0; }
    protected void keyPressed(int keyCode) {}
    protected void keyReleased(int keyCode) {}
    protected void keyRepeated(int keyCode) {}
    protected void showNotify() {}
    protected void hideNotify() {}
    public void setFullScreenMode(boolean b) {}
    public boolean isDoubleBuffered() { return true; }
}
