package javax.microedition.lcdui;
public class ImageItem extends Item {
    public static final int LAYOUT_DEFAULT = 0, LAYOUT_LEFT = 1, LAYOUT_RIGHT = 2, LAYOUT_CENTER = 3,
            LAYOUT_NEWLINE_BEFORE = 4, LAYOUT_NEWLINE_AFTER = 8;
    public static final int PLAIN = 0, HYPERLINK = 1, BUTTON = 2;
    public ImageItem(String label, Image img, int layout, String altText) {}
    public ImageItem(String label, Image img, int layout, String altText, int appearanceMode) {}
    public Image getImage() { return null; }
    public void setImage(Image img) {}
    public String getAltText() { return null; }
    public int getAppearanceMode() { return 0; }
}
