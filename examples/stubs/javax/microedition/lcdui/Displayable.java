package javax.microedition.lcdui;
public abstract class Displayable {
    public void setTitle(String s) {}
    public String getTitle() { return null; }
    public void addCommand(Command c) {}
    public void removeCommand(Command c) {}
    public void setCommandListener(CommandListener l) {}
    public boolean isShown() { return false; }
    public void setTicker(Ticker ticker) {}
    public Ticker getTicker() { return null; }
}
