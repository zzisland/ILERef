package testsmell.smell;

import com.github.javaparser.Range;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.CastExpr;
import com.github.javaparser.ast.expr.DoubleLiteralExpr;
import com.github.javaparser.ast.expr.EnclosedExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.IntegerLiteralExpr;
import com.github.javaparser.ast.expr.LongLiteralExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.expr.UnaryExpr;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;
import testsmell.AbstractSmell;
import testsmell.AssertionUtil;
import testsmell.TestMethod;
import testsmell.Util;
import thresholds.Thresholds;

import java.io.FileNotFoundException;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/** Detects numeric literal targets in supported assertion expressions. */
public class MagicNumberTest extends AbstractSmell {
    public static final String COUNT_KEY = "MagicNumberCount";
    public static final String TARGETS_KEY = "MagicNumberTargets";
    public static final String THRESHOLD_KEY = "MagicNumberThreshold";
    public static final String THRESHOLD_MODE_KEY = "MagicNumberThresholdMode";
    public static final String COMPARISON_KEY = "MagicNumberComparisonOperator";
    public static final String AMBIGUITIES_KEY = "MagicNumberMappingAmbiguities";
    public static final String DIRECT_COUNT_KEY = "MagicNumberDirectLiteralCount";
    public static final String NESTED_METHOD_COUNT_KEY = "MagicNumberNestedMethodCount";
    public static final String NESTED_CONSTRUCTOR_COUNT_KEY = "MagicNumberNestedConstructorCount";
    public static final int MAX_NESTED_ARGUMENT_DEPTH = 1;

    public enum NestingKind {
        DIRECT_ASSERTION_ARGUMENT,
        NESTED_METHOD_ARGUMENT,
        NESTED_CONSTRUCTOR_ARGUMENT
    }

    public MagicNumberTest(Thresholds thresholds) {
        super(thresholds);
        validateThreshold(thresholds.getMagicNumberTest());
    }

    private static void validateThreshold(int threshold) {
        if (threshold < 1) {
            throw new IllegalArgumentException(
                    "Magic Number Test threshold must be >= 1, but was " + threshold);
        }
    }

    @Override
    public String getSmellName() {
        return "Magic Number Test";
    }

    @Override
    public void runAnalysis(CompilationUnit testFileCompilationUnit,
                            CompilationUnit productionFileCompilationUnit,
                            String testFileName,
                            String productionFileName) throws FileNotFoundException {
        validateThreshold(thresholds.getMagicNumberTest());
        smellyElementsSet.clear();
        new ClassVisitor().visit(testFileCompilationUnit, null);
    }

    private class ClassVisitor extends VoidVisitorAdapter<Void> {
        @Override
        public void visit(MethodDeclaration method, Void arg) {
            if (!Util.isValidTestMethod(method)) {
                super.visit(method, arg);
                return;
            }

            MethodAnalysis analysis = analyze(method);
            TestMethod testMethod = new TestMethod(method.getNameAsString());
            int threshold = thresholds.getMagicNumberTest();
            testMethod.setSmell(analysis.targets.size() >= threshold);
            testMethod.addDataItem(COUNT_KEY, String.valueOf(analysis.targets.size()));
            testMethod.addDataItem(TARGETS_KEY, serializeTargets(analysis.targets));
            testMethod.addDataItem(THRESHOLD_KEY, String.valueOf(threshold));
            testMethod.addDataItem(THRESHOLD_MODE_KEY, thresholdMode(thresholds));
            testMethod.addDataItem(COMPARISON_KEY, ">=");
            testMethod.addDataItem(AMBIGUITIES_KEY, String.valueOf(analysis.mappingAmbiguities));
            testMethod.addDataItem(DIRECT_COUNT_KEY, String.valueOf(analysis.directTargets));
            testMethod.addDataItem(NESTED_METHOD_COUNT_KEY, String.valueOf(analysis.nestedMethodTargets));
            testMethod.addDataItem(NESTED_CONSTRUCTOR_COUNT_KEY,
                    String.valueOf(analysis.nestedConstructorTargets));
            smellyElementsSet.add(testMethod);
        }
    }

    private MethodAnalysis analyze(MethodDeclaration method) {
        final MethodAnalysis result = new MethodAnalysis();
        if (!method.getBody().isPresent()) return result;

        method.getBody().get().accept(new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodDeclaration nestedMethod, Void arg) {
                // Assertions in local or anonymous type methods do not belong to the enclosing test.
            }

            @Override
            public void visit(MethodCallExpr call, Void arg) {
                super.visit(call, arg);
                AssertionUtil.Resolution resolution = AssertionUtil.resolve(call);
                if (resolution.getSource() == AssertionUtil.AssertionSource.KNOWN_TEST_ASSERTION) {
                    collectAssertionTargets(call, resolution, result);
                }
            }
        }, null);
        return result;
    }

    private void collectAssertionTargets(MethodCallExpr assertion,
                                         AssertionUtil.Resolution resolution,
                                         MethodAnalysis result) {
        AssertionUtil.MessageInfo message = AssertionUtil.messageInfo(assertion);
        boolean unresolvedSignature = message.getPresence()
                == AssertionUtil.MessagePresence.MESSAGE_SLOT_UNRESOLVED;
        if (unresolvedSignature) result.mappingAmbiguities++;

        for (int argumentIndex = 0; argumentIndex < assertion.getArguments().size(); argumentIndex++) {
            Expression argument = assertion.getArgument(argumentIndex);
            if (isMessageArgument(argument, message)) continue;
            String role = unresolvedSignature ? "ASSERTION_SIGNATURE_UNRESOLVED"
                    : argumentRole(assertion, resolution.getFramework(), message, argumentIndex);

            NumericLiteral direct = numericLiteral(argument);
            if (direct != null) {
                addTarget(result, direct, assertion, argumentIndex, role,
                        NestingKind.DIRECT_ASSERTION_ARGUMENT);
                continue;
            }

            Expression unwrapped = unwrapTransparent(argument);
            if (unwrapped instanceof MethodCallExpr) {
                for (Expression nestedArgument : ((MethodCallExpr) unwrapped).getArguments()) {
                    NumericLiteral literal = numericLiteral(nestedArgument);
                    if (literal != null) addTarget(result, literal, assertion, argumentIndex, role,
                            NestingKind.NESTED_METHOD_ARGUMENT);
                }
            } else if (unwrapped instanceof ObjectCreationExpr) {
                for (Expression nestedArgument : ((ObjectCreationExpr) unwrapped).getArguments()) {
                    NumericLiteral literal = numericLiteral(nestedArgument);
                    if (literal != null) addTarget(result, literal, assertion, argumentIndex, role,
                            NestingKind.NESTED_CONSTRUCTOR_ARGUMENT);
                }
            }
        }
    }

    private static boolean isMessageArgument(Expression argument, AssertionUtil.MessageInfo message) {
        Optional<Expression> expression = message.getExpression();
        return message.getPresence() == AssertionUtil.MessagePresence.MESSAGE_PRESENT
                && expression.isPresent() && expression.get() == argument;
    }

    private static String argumentRole(MethodCallExpr assertion, AssertionUtil.Framework framework,
                                       AssertionUtil.MessageInfo message, int index) {
        String name = assertion.getNameAsString();
        boolean junit4Style = framework == AssertionUtil.Framework.JUNIT3
                || framework == AssertionUtil.Framework.JUNIT4
                || framework == AssertionUtil.Framework.HAMCREST;
        int valueIndex = index;
        if (junit4Style && message.getPresence() == AssertionUtil.MessagePresence.MESSAGE_PRESENT) valueIndex--;

        if ("assertThat".equals(name)) return valueIndex == 0 ? "ACTUAL" : "MATCHER";
        if ("assertTrue".equals(name) || "assertFalse".equals(name)
                || "assertNull".equals(name) || "assertNotNull".equals(name)) return "ACTUAL";
        if ("assertEquals".equals(name) || "assertNotEquals".equals(name)
                || "assertArrayEquals".equals(name)) {
            if (valueIndex == 0) return "EXPECTED";
            if (valueIndex == 1) return "ACTUAL";
            if (valueIndex == 2) return "DELTA";
        }
        if ("assertSame".equals(name) || "assertNotSame".equals(name)
                || "assertIterableEquals".equals(name) || "assertLinesMatch".equals(name)) {
            if (valueIndex == 0) return "EXPECTED";
            if (valueIndex == 1) return "ACTUAL";
        }
        if ("assertInstanceOf".equals(name)) return valueIndex == 0 ? "EXPECTED_TYPE" : "ACTUAL";
        if ("assertThrows".equals(name) || "assertThrowsExactly".equals(name)) {
            return valueIndex == 0 ? "EXPECTED_EXCEPTION_TYPE" : "EXECUTABLE";
        }
        return "ARGUMENT_" + index;
    }

    private static Expression unwrapTransparent(Expression expression) {
        Expression current = expression;
        while (current instanceof EnclosedExpr || current instanceof CastExpr) {
            current = current instanceof EnclosedExpr
                    ? ((EnclosedExpr) current).getInner().get() : ((CastExpr) current).getExpression();
        }
        return current;
    }

    private static NumericLiteral numericLiteral(Expression expression) {
        Expression current = expression;
        int sign = 1;
        boolean changed = true;
        while (changed) {
            changed = false;
            if (current instanceof EnclosedExpr) {
                current = ((EnclosedExpr) current).getInner().get();
                changed = true;
            } else if (current instanceof CastExpr) {
                current = ((CastExpr) current).getExpression();
                changed = true;
            } else if (current instanceof UnaryExpr) {
                UnaryExpr unary = (UnaryExpr) current;
                if (unary.getOperator() == UnaryExpr.Operator.MINUS
                        || unary.getOperator() == UnaryExpr.Operator.PLUS) {
                    if (unary.getOperator() == UnaryExpr.Operator.MINUS) sign = -sign;
                    current = unary.getExpression();
                    changed = true;
                }
            }
        }

        String kind;
        String raw;
        if (current instanceof IntegerLiteralExpr) {
            kind = "INTEGER_LITERAL";
            raw = ((IntegerLiteralExpr) current).getValue();
        } else if (current instanceof LongLiteralExpr) {
            kind = "LONG_LITERAL";
            raw = ((LongLiteralExpr) current).getValue();
        } else if (current instanceof DoubleLiteralExpr) {
            raw = ((DoubleLiteralExpr) current).getValue();
            kind = endsWithIgnoreCase(raw, "f") ? "FLOAT_LITERAL" : "DOUBLE_LITERAL";
        } else {
            return null;
        }
        return new NumericLiteral(expression, current, kind, normalize(raw, kind, sign));
    }

    private static String normalize(String rawValue, String kind, int sign) {
        String value = rawValue.replace("_", "");
        try {
            String normalized;
            if ("INTEGER_LITERAL".equals(kind) || "LONG_LITERAL".equals(kind)) {
                value = value.replaceFirst("(?i)[l]$", "");
                int radix = 10;
                if (value.matches("(?i)^0x.*")) { radix = 16; value = value.substring(2); }
                else if (value.matches("(?i)^0b.*")) { radix = 2; value = value.substring(2); }
                else if (value.length() > 1 && value.startsWith("0")) { radix = 8; value = value.substring(1); }
                normalized = new BigInteger(value.isEmpty() ? "0" : value, radix).toString();
            } else {
                value = value.replaceFirst("(?i)[fd]$", "");
                normalized = new BigDecimal(value).stripTrailingZeros().toPlainString();
            }
            if (sign < 0 && !"0".equals(normalized)) normalized = "-" + normalized;
            return normalized;
        } catch (NumberFormatException ignored) {
            return (sign < 0 ? "-" : "") + value.toLowerCase();
        }
    }

    private static boolean endsWithIgnoreCase(String value, String suffix) {
        return value.length() >= suffix.length()
                && value.regionMatches(true, value.length() - suffix.length(), suffix, 0, suffix.length());
    }

    private static void addTarget(MethodAnalysis result, NumericLiteral literal,
                                  MethodCallExpr assertion, int argumentIndex, String role,
                                  NestingKind nestingKind) {
        String range = range(literal.expression);
        if (!result.targetNodes.add(literal.literalNode)) return;
        if (nestingKind == NestingKind.DIRECT_ASSERTION_ARGUMENT) result.directTargets++;
        else if (nestingKind == NestingKind.NESTED_METHOD_ARGUMENT) result.nestedMethodTargets++;
        else if (nestingKind == NestingKind.NESTED_CONSTRUCTOR_ARGUMENT) result.nestedConstructorTargets++;
        result.targets.add(new TargetEvidence(literal.expression.toString(), literal.normalizedValue,
                literal.kind, assertion.getNameAsString(), argumentIndex, role,
                nestingKind.name(), range));
    }

    private static String range(Expression expression) {
        if (!expression.getRange().isPresent()) return "UNAVAILABLE";
        Range range = expression.getRange().get();
        return range.begin.line + ":" + range.begin.column + "-"
                + range.end.line + ":" + range.end.column;
    }

    private static String thresholdMode(Thresholds thresholds) {
        String simple = thresholds.getClass().getSimpleName();
        return simple.isEmpty() ? thresholds.getClass().getName() : simple;
    }

    private static String serializeTargets(List<TargetEvidence> targets) {
        StringBuilder json = new StringBuilder("[");
        for (int i = 0; i < targets.size(); i++) {
            if (i > 0) json.append(',');
            TargetEvidence target = targets.get(i);
            json.append('{')
                    .append(jsonField("literal_text", target.literalText)).append(',')
                    .append(jsonField("normalized_numeric_value", target.normalizedNumericValue)).append(',')
                    .append(jsonField("literal_kind", target.literalKind)).append(',')
                    .append(jsonField("assertion_name", target.assertionName)).append(',')
                    .append("\"argument_index\":").append(target.argumentIndex).append(',')
                    .append(jsonField("argument_role", target.argumentRole)).append(',')
                    .append(jsonField("nesting_kind", target.nestingKind)).append(',')
                    .append(jsonField("source_range", target.sourceRange))
                    .append('}');
        }
        return json.append(']').toString();
    }

    private static String jsonField(String name, String value) {
        return "\"" + name + "\":\"" + escapeJson(value) + "\"";
    }

    private static String escapeJson(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t");
    }

    private static final class NumericLiteral {
        private final Expression expression;
        private final Expression literalNode;
        private final String kind;
        private final String normalizedValue;
        private NumericLiteral(Expression expression, Expression literalNode, String kind, String normalizedValue) {
            this.expression = expression;
            this.literalNode = literalNode;
            this.kind = kind;
            this.normalizedValue = normalizedValue;
        }
    }

    private static final class TargetEvidence {
        private final String literalText;
        private final String normalizedNumericValue;
        private final String literalKind;
        private final String assertionName;
        private final int argumentIndex;
        private final String argumentRole;
        private final String nestingKind;
        private final String sourceRange;
        private TargetEvidence(String literalText, String normalizedNumericValue, String literalKind,
                               String assertionName, int argumentIndex, String argumentRole,
                               String nestingKind, String sourceRange) {
            this.literalText = literalText;
            this.normalizedNumericValue = normalizedNumericValue;
            this.literalKind = literalKind;
            this.assertionName = assertionName;
            this.argumentIndex = argumentIndex;
            this.argumentRole = argumentRole;
            this.nestingKind = nestingKind;
            this.sourceRange = sourceRange;
        }
    }

    private static final class MethodAnalysis {
        private final List<TargetEvidence> targets = new ArrayList<>();
        private final Set<Expression> targetNodes = Collections.newSetFromMap(
                new IdentityHashMap<Expression, Boolean>());
        private int mappingAmbiguities;
        private int directTargets;
        private int nestedMethodTargets;
        private int nestedConstructorTargets;
    }
}
