package javax.microedition.lcdui;
import javax.microedition.midlet.MIDlet;
public class Display {
    public static Display getDisplay(MIDlet m) { return null; }
    public void setCurrent(Displayable d) {}
    public void setCurrent(Alert alert, Displayable nextDisplayable) {}
    public void setCurrentItem(Item item) {}
    public Displayable getCurrent() { return null; }
    public boolean vibrate(int ms) { return false; }
    public boolean isColor() { return true; }
    public int numColors() { return 65536; }
}
