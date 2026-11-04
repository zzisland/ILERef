package testsmell.smell;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.BooleanLiteralExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NullLiteralExpr;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;
import testsmell.AbstractSmell;
import testsmell.AssertionUtil;
import testsmell.TestMethod;
import testsmell.Util;
import thresholds.Thresholds;

import java.io.FileNotFoundException;

/*
If a test method contains an assert statement that explicitly returns a true or false, the method is marked as smelly
 */
public class RedundantAssertion extends AbstractSmell {

    public RedundantAssertion(Thresholds thresholds) {
        super(thresholds);
    }

    /**
     * Checks of 'Redundant Assertion' smell
     */
    @Override
    public String getSmellName() {
        return "Redundant Assertion";
    }

    /**
     * Analyze the test file for test methods for multiple assert statements
     */
    @Override
    public void runAnalysis(CompilationUnit testFileCompilationUnit, CompilationUnit productionFileCompilationUnit, String testFileName, String productionFileName) throws FileNotFoundException {
        RedundantAssertion.ClassVisitor classVisitor;
        classVisitor = new RedundantAssertion.ClassVisitor();
        classVisitor.visit(testFileCompilationUnit, null);
    }

    private class ClassVisitor extends VoidVisitorAdapter<Void> {
        private MethodDeclaration currentMethod = null;
        private int redundantCount = 0;
        TestMethod testMethod;

        // examine all methods in the test class
        @Override
        public void visit(MethodDeclaration n, Void arg) {
            if (Util.isValidTestMethod(n)) {
                currentMethod = n;
                testMethod = new TestMethod(n.getNameAsString());
                testMethod.setSmell(false); //default value is false (i.e. no smell)
                super.visit(n, arg);

                boolean isSmelly = redundantCount > thresholds.getRedundantAssertion();
                testMethod.setSmell(isSmelly);
                testMethod.addDataItem("RedundantCount", String.valueOf(redundantCount));

                smellyElementsSet.add(testMethod);

                //reset values for next method
                currentMethod = null;
                redundantCount = 0;
            }
        }


        @Override
        public void visit(MethodCallExpr n, Void arg) {
            super.visit(n, arg);
            if (currentMethod != null && AssertionUtil.isAssertion(n)) {
                AssertionUtil.Framework framework = AssertionUtil.resolve(n).getFramework();
                switch (n.getNameAsString()) {
                    case "assertTrue":
                    case "assertFalse":
                        int booleanIndex = framework == AssertionUtil.Framework.JUNIT5 ? 0
                                : n.getArguments().size() > 1 ? 1 : 0;
                        if (n.getArguments().size() > booleanIndex
                                && n.getArgument(booleanIndex) instanceof BooleanLiteralExpr) redundantCount++;
                        break;

                    case "assertNotNull":
                    case "assertNull":
                        int nullIndex = framework == AssertionUtil.Framework.JUNIT5 ? 0
                                : n.getArguments().size() > 1 ? 1 : 0;
                        if (n.getArguments().size() > nullIndex
                                && n.getArgument(nullIndex) instanceof NullLiteralExpr) redundantCount++;
                        break;

                    case "assertEquals":
                    case "assertNotEquals":
                    case "assertSame":
                    case "assertNotSame":
                    case "assertArrayEquals":
                    case "assertIterableEquals":
                    case "assertLinesMatch":
                        int expectedIndex = 0;
                        if ((framework == AssertionUtil.Framework.JUNIT3 || framework == AssertionUtil.Framework.JUNIT4)
                                && n.getArguments().size() >= 3
                                && n.getArgument(0) instanceof com.github.javaparser.ast.expr.StringLiteralExpr) {
                            expectedIndex = 1;
                        }
                        if (n.getArguments().size() > expectedIndex + 1
                                && n.getArgument(expectedIndex).equals(n.getArgument(expectedIndex + 1)))
                            redundantCount++;
                        break;
                    default:
                        break;
                }
            }
        }

    }
}
