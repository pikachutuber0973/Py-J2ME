import javax.microedition.midlet.MIDlet;
import javax.microedition.lcdui.*;

public class TestGame extends MIDlet {
    private TestCanvas canvas;

    protected void startApp() {
        canvas = new TestCanvas();
        Display.getDisplay(this).setCurrent(canvas);
        canvas.runSelfTest();
        Thread t = new Thread(canvas);
        t.start();
    }

    protected void pauseApp() {}
    protected void destroyApp(boolean unconditional) {}
}

class TestCanvas extends Canvas implements Runnable {
    private int x = 0;
    private int score = 0;
    private int[] history = new int[5];
    private String status = "boot";
    private volatile boolean running = true;

    protected void paint(Graphics g) {
        g.setColor(255, 255, 255);
        g.fillRect(0, 0, getWidth(), getHeight());
        g.setColor(0, 0, 0);
        g.drawString("x=" + x + " score=" + score, 4, 4, Graphics.TOP | Graphics.LEFT);
        g.drawString(status, 4, 20, Graphics.TOP | Graphics.LEFT);
    }

    protected void keyPressed(int keyCode) {
        int action = getGameAction(keyCode);
        if (action == RIGHT) x = x + 5;
        else if (action == LEFT) x = x - 5;
        else if (action == FIRE) score = score + 1;
        repaint();
    }

    public void run() {
        int ticks = 0;
        while (running && ticks < 3) {
            x = x + 1;
            ticks = ticks + 1;
            try {
                Thread.sleep(10);
            } catch (InterruptedException e) {
                status = "interrupted";
            }
        }
        status = "loop done";
        repaint();
    }

    /** Exercises arithmetic, arrays, StringBuffer, exceptions, and
     * instance/static method dispatch -- results are written into
     * `status` so the outer test harness can read them back via
     * getStatus() after running. */
    void runSelfTest() {
        int sum = 0;
        for (int i = 0; i < 5; i++) {
            history[i] = i * i;
            sum += history[i];
        }
        int fact = factorial(6);
        StringBuffer sb = new StringBuffer();
        sb.append("sum=").append(sum).append(" fact=").append(fact);
        boolean caught = false;
        try {
            int bad = 10 / zero();
        } catch (ArithmeticException e) {
            caught = true;
        }
        boolean boundsCaught = false;
        try {
            int v = history[99];
        } catch (ArrayIndexOutOfBoundsException e) {
            boundsCaught = true;
        }
        sb.append(" divzero=").append(caught).append(" bounds=").append(boundsCaught);
        MathHelper helper = new MathHelper(7);
        sb.append(" double=").append(helper.doubled());
        status = sb.toString();
    }

    private int zero() { return 0; }

    static int factorial(int n) {
        if (n <= 1) return 1;
        return n * factorial(n - 1);
    }

    String getStatus() { return status; }
    int getX() { return x; }
    int getScore() { return score; }
}

class MathHelper {
    private int value;
    MathHelper(int v) { this.value = v; }
    int doubled() { return value * 2; }
}
