package testsmell;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.BinaryExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.DoubleLiteralExpr;
import com.github.javaparser.ast.expr.IntegerLiteralExpr;
import com.github.javaparser.ast.expr.LambdaExpr;
import com.github.javaparser.ast.expr.LongLiteralExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.MethodReferenceExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.expr.StringLiteralExpr;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.WeakHashMap;

/** Conservative, import-aware recognition of supported test assertion calls. */
public final class AssertionUtil {
    public enum AssertionSource { KNOWN_TEST_ASSERTION, NON_TEST_ASSERTION, UNRESOLVED_ASSERTION }
    public enum Framework { JUNIT3, JUNIT4, JUNIT5, HAMCREST, NONE }
    public enum ResolutionReason {
        QUALIFIED_JUNIT_OWNER, EXPLICIT_JUNIT_STATIC_IMPORT, JUNIT_WILDCARD_NO_CONFLICT,
        JUNIT3_TESTCASE_INHERITANCE, LOCAL_METHOD_SHADOWING, EXPLICIT_NON_JUNIT_IMPORT,
        OTHER_OWNER, WILDCARD_CONFLICT_UNRESOLVED, SOURCE_UNAVAILABLE, UNSUPPORTED_METHOD,
        NO_COMPILATION_UNIT, AMBIGUOUS_JUNIT_IMPORTS, UNQUALIFIED_WITHOUT_SOURCE
    }
    public enum MessagePresence { MESSAGE_PRESENT, MESSAGE_ABSENT, MESSAGE_SLOT_UNRESOLVED }
    public enum MessageValueKind {
        STATIC_LITERAL, CONSTANT_EXPRESSION, DYNAMIC_EXPRESSION,
        SUPPLIER_EXPRESSION, VALUE_UNRESOLVED, NONE
    }
    /** Compatibility view retained for callers of the first hardening round. */
    public enum MessageKind { NONE, STRING_LITERAL, EXPRESSION, SUPPLIER, UNKNOWN }

    public static final class Resolution {
        private final AssertionSource source;
        private final Framework framework;
        private final ResolutionReason reason;
        private Resolution(AssertionSource source, Framework framework, ResolutionReason reason) {
            this.source = source; this.framework = framework; this.reason = reason;
        }
        public AssertionSource getSource() { return source; }
        public Framework getFramework() { return framework; }
        public String getReason() { return reason.name(); }
        public ResolutionReason getReasonCode() { return reason; }
    }

    public static final class MessageInfo {
        private final MessagePresence presence;
        private final MessageValueKind valueKind;
        private final Expression expression;
        private MessageInfo(MessagePresence presence, MessageValueKind valueKind, Expression expression) {
            this.presence = presence; this.valueKind = valueKind; this.expression = expression;
        }
        public MessagePresence getPresence() { return presence; }
        public MessageValueKind getValueKind() { return valueKind; }
        public Optional<Expression> getExpression() { return Optional.ofNullable(expression); }
        public Optional<String> getStaticString() { return constantString(expression); }
        public boolean isPresent() { return presence == MessagePresence.MESSAGE_PRESENT; }
        public boolean isComparable() { return getStaticString().isPresent(); }
        public MessageKind getKind() {
            if (presence == MessagePresence.MESSAGE_ABSENT) return MessageKind.NONE;
            if (presence == MessagePresence.MESSAGE_SLOT_UNRESOLVED) return MessageKind.UNKNOWN;
            if (valueKind == MessageValueKind.STATIC_LITERAL) return MessageKind.STRING_LITERAL;
            if (valueKind == MessageValueKind.SUPPLIER_EXPRESSION) return MessageKind.SUPPLIER;
            return MessageKind.EXPRESSION;
        }
    }

    private enum Conflict { DECLARES_METHOD, NO_METHOD, SOURCE_UNAVAILABLE }
    private enum ExpressionType { STRING, SUPPLIER, NUMERIC, THROWABLE, OTHER, UNKNOWN }
    private static final String JUNIT4_ASSERT = "org.junit.Assert";
    private static final String JUNIT5_ASSERTIONS = "org.junit.jupiter.api.Assertions";
    private static final String JUNIT3_TEST_CASE = "junit.framework.TestCase";
    private static final String HAMCREST_MATCHER_ASSERT = "org.hamcrest.MatcherAssert";
    private static final Set<String> ASSERTION_NAMES = Collections.unmodifiableSet(new HashSet<>(Arrays.asList(
            "assertEquals", "assertNotEquals", "assertTrue", "assertFalse", "assertNull", "assertNotNull",
            "assertSame", "assertNotSame", "assertArrayEquals", "assertIterableEquals", "assertLinesMatch",
            "assertInstanceOf", "assertThrows", "assertThrowsExactly", "assertDoesNotThrow", "assertTimeout",
            "assertTimeoutPreemptively", "assertAll", "fail", "assertThat")));
    private static final Set<String> UNARY = set("assertTrue", "assertFalse", "assertNull", "assertNotNull",
            "assertDoesNotThrow");
    private static final Set<String> BINARY = set("assertEquals", "assertNotEquals", "assertSame", "assertNotSame",
            "assertArrayEquals", "assertIterableEquals", "assertLinesMatch", "assertInstanceOf", "assertThrows",
            "assertThrowsExactly", "assertTimeout", "assertTimeoutPreemptively");
    private static final Set<String> DELTA_APIS = set("assertEquals", "assertNotEquals", "assertArrayEquals");
    private static final Set<String> NUMERIC_TYPES = set("byte", "short", "int", "long", "float", "double",
            "Byte", "Short", "Integer", "Long", "Float", "Double", "BigDecimal", "BigInteger", "Number");
    private static final Map<CompilationUnit, List<Path>> SOURCE_ROOTS =
            Collections.synchronizedMap(new WeakHashMap<CompilationUnit, List<Path>>());

    private AssertionUtil() { }

    /** Registers project source roots used to prove whether another wildcard owner conflicts. */
    public static void registerSourceRoots(CompilationUnit unit, List<Path> roots) {
        List<Path> normalizedRoots = new ArrayList<>();
        for (Path root : roots) {
            if (root == null) continue;
            Path normalized = root.toAbsolutePath().normalize();
            if (!normalizedRoots.contains(normalized)) normalizedRoots.add(normalized);
        }
        SOURCE_ROOTS.put(unit, Collections.unmodifiableList(normalizedRoots));
    }

    public static boolean isAssertion(MethodCallExpr call) {
        return resolve(call).getSource() == AssertionSource.KNOWN_TEST_ASSERTION;
    }

    public static Resolution resolve(MethodCallExpr call) {
        String name = call.getNameAsString();
        if (!ASSERTION_NAMES.contains(name))
            return resolution(AssertionSource.NON_TEST_ASSERTION, Framework.NONE, ResolutionReason.UNSUPPORTED_METHOD);
        CompilationUnit unit = compilationUnitOf(call);
        if (unit == null)
            return resolution(AssertionSource.UNRESOLVED_ASSERTION, Framework.NONE, ResolutionReason.NO_COMPILATION_UNIT);
        if (call.getScope().isPresent()) {
            Framework framework = frameworkForScope(call.getScope().get().toString(), unit);
            if (framework != Framework.NONE && supportedByFramework(name, framework))
                return resolution(AssertionSource.KNOWN_TEST_ASSERTION, framework, ResolutionReason.QUALIFIED_JUNIT_OWNER);
            return resolution(AssertionSource.NON_TEST_ASSERTION, Framework.NONE, ResolutionReason.OTHER_OWNER);
        }
        if (declaredInEnclosingOrParentType(call, unit, name))
            return resolution(AssertionSource.NON_TEST_ASSERTION, Framework.NONE, ResolutionReason.LOCAL_METHOD_SHADOWING);

        Set<Framework> explicitJUnit = new HashSet<>();
        Set<Framework> wildcardJUnit = new HashSet<>();
        List<String> otherWildcards = new ArrayList<>();
        boolean explicitNonJUnit = false;
        for (ImportDeclaration declaration : unit.getImports()) {
            if (!declaration.isStatic()) continue;
            String imported = declaration.getNameAsString();
            if (declaration.isAsterisk()) {
                Framework framework = frameworkForStaticImport(imported, true);
                if (framework != Framework.NONE && supportedByFramework(name, framework)) wildcardJUnit.add(framework);
                else otherWildcards.add(imported);
            } else if (imported.endsWith("." + name)) {
                Framework framework = frameworkForStaticImport(imported, false);
                if (framework != Framework.NONE && supportedByFramework(name, framework)) explicitJUnit.add(framework);
                else explicitNonJUnit = true;
            }
        }
        if (explicitNonJUnit)
            return resolution(AssertionSource.NON_TEST_ASSERTION, Framework.NONE, ResolutionReason.EXPLICIT_NON_JUNIT_IMPORT);
        if (explicitJUnit.size() == 1)
            return resolution(AssertionSource.KNOWN_TEST_ASSERTION, explicitJUnit.iterator().next(),
                    ResolutionReason.EXPLICIT_JUNIT_STATIC_IMPORT);
        if (explicitJUnit.size() > 1)
            return resolution(AssertionSource.UNRESOLVED_ASSERTION, Framework.NONE, ResolutionReason.AMBIGUOUS_JUNIT_IMPORTS);
        if (wildcardJUnit.size() == 1) {
            boolean unavailable = false;
            for (String owner : otherWildcards) {
                Conflict conflict = wildcardConflict(unit, owner, name);
                if (conflict == Conflict.DECLARES_METHOD)
                    return resolution(AssertionSource.UNRESOLVED_ASSERTION, Framework.NONE,
                            ResolutionReason.WILDCARD_CONFLICT_UNRESOLVED);
                unavailable |= conflict == Conflict.SOURCE_UNAVAILABLE;
            }
            if (unavailable)
                return resolution(AssertionSource.UNRESOLVED_ASSERTION, Framework.NONE, ResolutionReason.SOURCE_UNAVAILABLE);
            return resolution(AssertionSource.KNOWN_TEST_ASSERTION, wildcardJUnit.iterator().next(),
                    ResolutionReason.JUNIT_WILDCARD_NO_CONFLICT);
        }
        if (wildcardJUnit.size() > 1)
            return resolution(AssertionSource.UNRESOLVED_ASSERTION, Framework.NONE, ResolutionReason.AMBIGUOUS_JUNIT_IMPORTS);
        if (!otherWildcards.isEmpty())
            return resolution(AssertionSource.NON_TEST_ASSERTION, Framework.NONE, ResolutionReason.OTHER_OWNER);
        if (inheritsJUnit3TestCase(call, unit))
            return resolution(AssertionSource.KNOWN_TEST_ASSERTION, Framework.JUNIT3,
                    ResolutionReason.JUNIT3_TESTCASE_INHERITANCE);
        return resolution(AssertionSource.UNRESOLVED_ASSERTION, Framework.NONE,
                ResolutionReason.UNQUALIFIED_WITHOUT_SOURCE);
    }

    public static boolean hasMessage(MethodCallExpr call) {
        return messageInfo(call).getPresence() == MessagePresence.MESSAGE_PRESENT;
    }

    /** Metadata-driven message-slot and value classification. */
    public static MessageInfo messageInfo(MethodCallExpr call) {
        Resolution resolution = resolve(call);
        if (resolution.source != AssertionSource.KNOWN_TEST_ASSERTION)
            return unresolved(null);
        String name = call.getNameAsString();
        int size = call.getArguments().size();
        if ("assertThat".equals(name)) return size >= 3 ? present(call.getArgument(0), call) : absent();
        if ("assertAll".equals(name)) return assertAllMessage(call, resolution.framework);
        if ("fail".equals(name)) return failMessage(call, resolution.framework);
        if (resolution.framework == Framework.JUNIT3 || resolution.framework == Framework.JUNIT4)
            return junit4Message(call, name, size);
        if (resolution.framework == Framework.JUNIT5) return junit5Message(call, name, size);
        return unresolved(null);
    }

    private static MessageInfo junit4Message(MethodCallExpr call, String name, int size) {
        int base = UNARY.contains(name) ? 1 : BINARY.contains(name) ? 2 : -1;
        if (base < 0) return unresolved(null);
        if (size <= base) return absent();
        Expression first = call.getArgument(0);
        if (size >= base + 2) return present(first, call);
        if (!DELTA_APIS.contains(name) || UNARY.contains(name)) return present(first, call);
        ExpressionType type = expressionType(first, call);
        if (type == ExpressionType.STRING || type == ExpressionType.SUPPLIER) return present(first, call);
        if (type == ExpressionType.NUMERIC) return absent();
        return unresolved(first);
    }

    private static MessageInfo junit5Message(MethodCallExpr call, String name, int size) {
        int base = UNARY.contains(name) ? 1 : BINARY.contains(name) ? 2 : -1;
        if (base < 0) return unresolved(null);
        if (size <= base) return absent();
        Expression last = call.getArgument(size - 1);
        if (size >= base + 2) return present(last, call);
        if (!DELTA_APIS.contains(name)) return present(last, call);
        ExpressionType type = expressionType(last, call);
        if (type == ExpressionType.NUMERIC) return absent();
        if (type == ExpressionType.STRING || type == ExpressionType.SUPPLIER) return present(last, call);
        return unresolved(last);
    }

    private static MessageInfo assertAllMessage(MethodCallExpr call, Framework framework) {
        if (framework != Framework.JUNIT5) return unresolved(null);
        if (call.getArguments().isEmpty()) return absent();
        Expression first = call.getArgument(0);
        if (first instanceof LambdaExpr || first instanceof MethodReferenceExpr) return absent();
        return expressionType(first, call) == ExpressionType.STRING ? present(first, call) : unresolved(first);
    }

    private static MessageInfo failMessage(MethodCallExpr call, Framework framework) {
        int size = call.getArguments().size();
        if (size == 0) return absent();
        Expression first = call.getArgument(0);
        if (framework == Framework.JUNIT3 || framework == Framework.JUNIT4) return present(first, call);
        if (size >= 2) return present(first, call);
        ExpressionType type = expressionType(first, call);
        if (type == ExpressionType.THROWABLE || first instanceof ObjectCreationExpr) return absent();
        if (type == ExpressionType.STRING || type == ExpressionType.SUPPLIER) return present(first, call);
        return unresolved(first);
    }

    private static MessageInfo present(Expression expression, MethodCallExpr call) {
        return message(MessagePresence.MESSAGE_PRESENT, valueKind(expression, call), expression);
    }
    private static MessageInfo absent() {
        return message(MessagePresence.MESSAGE_ABSENT, MessageValueKind.NONE, null);
    }
    private static MessageInfo unresolved(Expression expression) {
        return message(MessagePresence.MESSAGE_SLOT_UNRESOLVED, MessageValueKind.VALUE_UNRESOLVED, expression);
    }

    private static MessageValueKind valueKind(Expression expression, MethodCallExpr call) {
        if (expression instanceof StringLiteralExpr) return MessageValueKind.STATIC_LITERAL;
        if (constantString(expression).isPresent()) return MessageValueKind.CONSTANT_EXPRESSION;
        if (expression instanceof LambdaExpr || expression instanceof MethodReferenceExpr)
            return MessageValueKind.SUPPLIER_EXPRESSION;
        return expressionType(expression, call) == ExpressionType.UNKNOWN
                ? MessageValueKind.VALUE_UNRESOLVED : MessageValueKind.DYNAMIC_EXPRESSION;
    }

    private static Optional<String> constantString(Expression expression) {
        if (expression instanceof StringLiteralExpr)
            return Optional.of(((StringLiteralExpr) expression).asString());
        if (expression instanceof BinaryExpr && ((BinaryExpr) expression).getOperator() == BinaryExpr.Operator.PLUS) {
            Optional<String> left = constantString(((BinaryExpr) expression).getLeft());
            Optional<String> right = constantString(((BinaryExpr) expression).getRight());
            if (left.isPresent() && right.isPresent()) return Optional.of(left.get() + right.get());
        }
        return Optional.empty();
    }

    private static ExpressionType expressionType(Expression expression, MethodCallExpr context) {
        if (expression instanceof StringLiteralExpr) return ExpressionType.STRING;
        if (expression instanceof LambdaExpr || expression instanceof MethodReferenceExpr) return ExpressionType.SUPPLIER;
        if (expression instanceof IntegerLiteralExpr || expression instanceof LongLiteralExpr
                || expression instanceof DoubleLiteralExpr)
            return ExpressionType.NUMERIC;
        if (expression instanceof ObjectCreationExpr) {
            String type = ((ObjectCreationExpr) expression).getType().getNameAsString();
            if ("String".equals(type)) return ExpressionType.STRING;
            if (type.endsWith("Exception") || type.endsWith("Error") || type.endsWith("Throwable"))
                return ExpressionType.THROWABLE;
            return ExpressionType.OTHER;
        }
        if (expression instanceof BinaryExpr && ((BinaryExpr) expression).getOperator() == BinaryExpr.Operator.PLUS) {
            ExpressionType left = expressionType(((BinaryExpr) expression).getLeft(), context);
            ExpressionType right = expressionType(((BinaryExpr) expression).getRight(), context);
            if (left == ExpressionType.STRING || right == ExpressionType.STRING) return ExpressionType.STRING;
            if (left == ExpressionType.NUMERIC && right == ExpressionType.NUMERIC) return ExpressionType.NUMERIC;
            return ExpressionType.UNKNOWN;
        }
        CompilationUnit unit = compilationUnitOf(context);
        if (expression instanceof NameExpr && unit != null)
            return declaredType(unit, ((NameExpr) expression).getNameAsString());
        if (expression instanceof MethodCallExpr && unit != null) {
            MethodCallExpr method = (MethodCallExpr) expression;
            if ("format".equals(method.getNameAsString()) && method.getScope().isPresent()
                    && "String".equals(method.getScope().get().toString())) return ExpressionType.STRING;
            return methodReturnType(unit, method.getNameAsString());
        }
        return ExpressionType.UNKNOWN;
    }

    private static ExpressionType declaredType(CompilationUnit unit, final String name) {
        final ExpressionType[] result = {ExpressionType.UNKNOWN};
        new VoidVisitorAdapter<Void>() {
            @Override public void visit(VariableDeclarator variable, Void arg) {
                super.visit(variable, arg);
                if (name.equals(variable.getNameAsString())) result[0] = typeName(variable.getType().toString());
            }
        }.visit(unit, null);
        return result[0];
    }

    private static ExpressionType methodReturnType(CompilationUnit unit, final String name) {
        final ExpressionType[] result = {ExpressionType.UNKNOWN};
        new VoidVisitorAdapter<Void>() {
            @Override public void visit(MethodDeclaration method, Void arg) {
                super.visit(method, arg);
                if (name.equals(method.getNameAsString())) result[0] = typeName(method.getType().toString());
            }
        }.visit(unit, null);
        return result[0];
    }

    private static ExpressionType typeName(String raw) {
        String type = raw.replace("java.lang.", "").replace("java.util.function.", "");
        if ("String".equals(type) || "CharSequence".equals(type)) return ExpressionType.STRING;
        if (type.startsWith("Supplier<") || "Supplier".equals(type)) return ExpressionType.SUPPLIER;
        if (NUMERIC_TYPES.contains(type)) return ExpressionType.NUMERIC;
        if (type.endsWith("Exception") || type.endsWith("Error") || type.endsWith("Throwable"))
            return ExpressionType.THROWABLE;
        return ExpressionType.UNKNOWN;
    }

    private static boolean supportedByFramework(String name, Framework framework) {
        if (framework == Framework.HAMCREST) return "assertThat".equals(name);
        if (framework == Framework.JUNIT5) return !"assertThat".equals(name);
        return true;
    }

    private static Framework frameworkForScope(String scope, CompilationUnit unit) {
        if (JUNIT5_ASSERTIONS.equals(scope)) return Framework.JUNIT5;
        if (JUNIT4_ASSERT.equals(scope)) return Framework.JUNIT4;
        if (HAMCREST_MATCHER_ASSERT.equals(scope)) return Framework.HAMCREST;
        if ("Assertions".equals(scope)) return importedType(unit, JUNIT5_ASSERTIONS) ? Framework.JUNIT5 : Framework.NONE;
        if ("Assert".equals(scope)) return importedType(unit, JUNIT4_ASSERT) ? Framework.JUNIT4 : Framework.NONE;
        if ("MatcherAssert".equals(scope)) return importedType(unit, HAMCREST_MATCHER_ASSERT) ? Framework.HAMCREST : Framework.NONE;
        return Framework.NONE;
    }

    private static Framework frameworkForStaticImport(String imported, boolean wildcard) {
        String owner = wildcard ? imported : imported.substring(0, imported.lastIndexOf('.'));
        if (JUNIT5_ASSERTIONS.equals(owner)) return Framework.JUNIT5;
        if (JUNIT4_ASSERT.equals(owner)) return Framework.JUNIT4;
        if (HAMCREST_MATCHER_ASSERT.equals(owner)) return Framework.HAMCREST;
        return Framework.NONE;
    }

    private static Conflict wildcardConflict(CompilationUnit unit, String owner, final String methodName) {
        Path source = sourceForOwner(unit, owner);
        if (source == null) return Conflict.SOURCE_UNAVAILABLE;
        try {
            CompilationUnit ownerUnit = JavaParser.parse(source, StandardCharsets.UTF_8);
            final boolean[] found = {false};
            new VoidVisitorAdapter<Void>() {
                @Override public void visit(MethodDeclaration method, Void arg) {
                    super.visit(method, arg);
                    if (methodName.equals(method.getNameAsString()) && method.isStatic()) found[0] = true;
                }
            }.visit(ownerUnit, null);
            return found[0] ? Conflict.DECLARES_METHOD : Conflict.NO_METHOD;
        } catch (IOException | RuntimeException exception) {
            return Conflict.SOURCE_UNAVAILABLE;
        }
    }

    private static Path sourceForOwner(CompilationUnit unit, String owner) {
        List<Path> roots = SOURCE_ROOTS.get(unit);
        if (roots == null || roots.isEmpty()) return null;
        String candidate = owner;
        while (candidate.contains(".")) {
            Path relative = Path.of(candidate.replace('.', '/') + ".java");
            for (Path root : roots) {
                Path path = root.resolve(relative).normalize();
                if (path.startsWith(root) && Files.isRegularFile(path)) return path;
            }
            candidate = candidate.substring(0, candidate.lastIndexOf('.'));
        }
        return null;
    }

    private static boolean importedType(CompilationUnit unit, String qualifiedType) {
        String packageName = qualifiedType.substring(0, qualifiedType.lastIndexOf('.'));
        for (ImportDeclaration declaration : unit.getImports()) {
            if (declaration.isStatic()) continue;
            String imported = declaration.getNameAsString();
            if ((!declaration.isAsterisk() && qualifiedType.equals(imported))
                    || (declaration.isAsterisk() && packageName.equals(imported))) return true;
        }
        return false;
    }

    private static boolean declaredInEnclosingOrParentType(MethodCallExpr call, CompilationUnit unit, String name) {
        Node node = call;
        while ((node = node.getParentNode().orElse(null)) != null) {
            if (node instanceof ClassOrInterfaceDeclaration) {
                ClassOrInterfaceDeclaration type = (ClassOrInterfaceDeclaration) node;
                if (!type.getMethodsByName(name).isEmpty()) return true;
                for (com.github.javaparser.ast.type.ClassOrInterfaceType extended : type.getExtendedTypes()) {
                    Optional<ClassOrInterfaceDeclaration> parent = unit.getClassByName(extended.getNameAsString());
                    if (parent.isPresent() && !parent.get().getMethodsByName(name).isEmpty()) return true;
                }
            }
        }
        return false;
    }

    private static boolean inheritsJUnit3TestCase(MethodCallExpr call, CompilationUnit unit) {
        Node node = call;
        while ((node = node.getParentNode().orElse(null)) != null) {
            if (node instanceof ClassOrInterfaceDeclaration) {
                ClassOrInterfaceDeclaration type = (ClassOrInterfaceDeclaration) node;
                for (com.github.javaparser.ast.type.ClassOrInterfaceType extended : type.getExtendedTypes()) {
                    String name = extended.getNameAsString();
                    if (JUNIT3_TEST_CASE.equals(extended.toString())
                            || ("TestCase".equals(name) && importedType(unit, JUNIT3_TEST_CASE))) return true;
                }
            }
        }
        return false;
    }

    private static CompilationUnit compilationUnitOf(Node node) {
        Node current = node;
        while (current != null) {
            if (current instanceof CompilationUnit) return (CompilationUnit) current;
            current = current.getParentNode().orElse(null);
        }
        return null;
    }

    private static Resolution resolution(AssertionSource source, Framework framework, ResolutionReason reason) {
        return new Resolution(source, framework, reason);
    }
    private static MessageInfo message(MessagePresence presence, MessageValueKind kind, Expression expression) {
        return new MessageInfo(presence, kind, expression);
    }
    private static Set<String> set(String... values) {
        return Collections.unmodifiableSet(new HashSet<>(Arrays.asList(values)));
    }
}
