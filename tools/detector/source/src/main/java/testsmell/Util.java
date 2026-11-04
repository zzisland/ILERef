package testsmell;

import com.github.javaparser.ast.body.MethodDeclaration;

public class Util {

    public static boolean isValidTestMethod(MethodDeclaration n) {
        return TestFrameworkDetector.isValidTestMethod(n);
    }

    public static boolean isValidSetupMethod(MethodDeclaration n) {
        return TestFrameworkDetector.isBeforeMethod(n)
                || (n.getNameAsString().equals("setUp")
                && n.getModifiers().contains(com.github.javaparser.ast.Modifier.PUBLIC));
    }

    public static boolean isInt(String s)
    {
        try
        { int i = Integer.parseInt(s); return true; }

        catch(NumberFormatException er)
        { return false; }
    }

    public static boolean isNumber(String str) {
        try {
            double v = Double.parseDouble(str);
            return true;
        } catch (NumberFormatException nfe) {
        }
        return false;
    }
}
