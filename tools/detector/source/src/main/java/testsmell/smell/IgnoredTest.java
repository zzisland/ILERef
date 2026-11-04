package testsmell.smell;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;
import testsmell.AbstractSmell;
import testsmell.SmellyElement;
import testsmell.TestClass;
import testsmell.TestMethod;
import testsmell.TestFrameworkDetector;
import thresholds.Thresholds;

import java.io.FileNotFoundException;
import java.util.List;

public class IgnoredTest extends AbstractSmell {

    public IgnoredTest(Thresholds thresholds) {
        super(thresholds);
    }

    /**
     * Checks of 'Ignored Test' smell
     */
    @Override
    public String getSmellName() {
        return "IgnoredTest";
    }

    /**
     * Analyze the test file for test methods that contain Ignored test methods
     */
    @Override
    public void runAnalysis(CompilationUnit testFileCompilationUnit, CompilationUnit productionFileCompilationUnit, String testFileName, String productionFileName) throws FileNotFoundException {
        IgnoredTest.ClassVisitor classVisitor;
        classVisitor = new IgnoredTest.ClassVisitor();
        classVisitor.visit(testFileCompilationUnit, null);
    }

    /**
     * Visitor class
     */
    private class ClassVisitor extends VoidVisitorAdapter<Void> {
        TestMethod testMethod;
        TestClass testClass;

        /**
         * This method will check if the class has the @Ignore annotation
         */
        @Override
        public void visit(ClassOrInterfaceDeclaration n, Void arg) {
            if (TestFrameworkDetector.isDisabled(n)) {
                testClass = new TestClass(n.getNameAsString());
                testClass.setHasSmell(true);
                smellyElementsSet.add(testClass);
            }
            super.visit(n, arg);
        }

        /**
         * The purpose of this method is to 'visit' all test methods in the test file.
         */
        @Override
        public void visit(MethodDeclaration n, Void arg) {

            // Class-level disabling is represented once by TestClass; avoid double-counting each contained method.
            if (TestFrameworkDetector.isTestMethod(n) && TestFrameworkDetector.isDirectlyDisabled(n)) {
                testMethod = new TestMethod(n.getNameAsString());
                testMethod.setSmell(true);
                smellyElementsSet.add(testMethod);
            }
        }

    }
}
