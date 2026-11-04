package testsmell;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.Modifier;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.nodeTypes.NodeWithAnnotations;
import com.github.javaparser.ast.type.VoidType;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Optional;
import java.util.Set;

/**
 * Centralized, import-aware classification of JUnit 3, JUnit 4 and JUnit 5 methods.
 * Smell implementations must use this class instead of matching annotation simple names.
 */
public final class TestFrameworkDetector {

    public enum TestFramework {
        JUNIT3, JUNIT4, JUNIT5, NONE
    }

    private static final String JUNIT4_TEST = "org.junit.Test";
    private static final String JUNIT5_TEST_FACTORY = "org.junit.jupiter.api.TestFactory";
    private static final Set<String> JUNIT5_TESTS = unmodifiableSet(
            "org.junit.jupiter.api.Test",
            "org.junit.jupiter.params.ParameterizedTest",
            "org.junit.jupiter.api.RepeatedTest",
            "org.junit.jupiter.api.TestFactory",
            "org.junit.jupiter.api.TestTemplate"
    );
    private static final Set<String> DISABLED = unmodifiableSet(
            "org.junit.Ignore",
            "org.junit.jupiter.api.Disabled"
    );
    private static final Set<String> BEFORE_METHODS = unmodifiableSet(
            "org.junit.Before",
            "org.junit.BeforeClass",
            "org.junit.jupiter.api.BeforeEach",
            "org.junit.jupiter.api.BeforeAll"
    );
    private static final Set<String> AFTER_METHODS = unmodifiableSet(
            "org.junit.After",
            "org.junit.AfterClass",
            "org.junit.jupiter.api.AfterEach",
            "org.junit.jupiter.api.AfterAll"
    );

    private TestFrameworkDetector() {
    }

    public static TestFramework frameworkOf(MethodDeclaration method) {
        if (hasAnnotation(method, JUNIT4_TEST)) {
            return hasJUnit4Signature(method) ? TestFramework.JUNIT4 : TestFramework.NONE;
        }
        if (hasAnyAnnotation(method, JUNIT5_TESTS)) {
            boolean validSignature = hasAnnotation(method, JUNIT5_TEST_FACTORY)
                    ? hasJUnit5FactorySignature(method) : hasJUnit5Signature(method);
            return validSignature ? TestFramework.JUNIT5 : TestFramework.NONE;
        }
        if (isJUnit3Method(method)) {
            return TestFramework.JUNIT3;
        }
        return TestFramework.NONE;
    }

    /** Includes disabled tests; use {@link #isValidTestMethod(MethodDeclaration)} for smell analysis. */
    public static boolean isTestMethod(MethodDeclaration method) {
        return frameworkOf(method) != TestFramework.NONE;
    }

    /** A runnable test method that should participate in ordinary smell rules. */
    public static boolean isValidTestMethod(MethodDeclaration method) {
        return isTestMethod(method) && !isDisabled(method);
    }

    public static boolean isDisabled(MethodDeclaration method) {
        if (isDirectlyDisabled(method)) {
            return true;
        }
        Optional<Node> parent = method.getParentNode();
        while (parent.isPresent()) {
            Node node = parent.get();
            if (node instanceof ClassOrInterfaceDeclaration
                    && hasAnyAnnotation((ClassOrInterfaceDeclaration) node, DISABLED)) {
                return true;
            }
            parent = node.getParentNode();
        }
        return false;
    }

    public static boolean isDirectlyDisabled(MethodDeclaration method) {
        return hasAnyAnnotation(method, DISABLED);
    }

    public static boolean isDisabled(ClassOrInterfaceDeclaration declaration) {
        return hasAnyAnnotation(declaration, DISABLED);
    }

    public static boolean isBeforeMethod(MethodDeclaration method) {
        return hasAnyAnnotation(method, BEFORE_METHODS) && hasLifecycleSignature(method);
    }

    public static boolean isAfterMethod(MethodDeclaration method) {
        return hasAnyAnnotation(method, AFTER_METHODS) && hasLifecycleSignature(method);
    }

    public static boolean isLifecycleMethod(MethodDeclaration method) {
        return isBeforeMethod(method) || isAfterMethod(method);
    }

    private static boolean isJUnit3Method(MethodDeclaration method) {
        return method.getNameAsString().toLowerCase().startsWith("test")
                && method.getModifiers().contains(Modifier.PUBLIC)
                && !method.getModifiers().contains(Modifier.STATIC)
                && method.getType() instanceof VoidType
                && method.getParameters().isEmpty();
    }

    private static boolean hasJUnit4Signature(MethodDeclaration method) {
        return method.getModifiers().contains(Modifier.PUBLIC)
                && !method.getModifiers().contains(Modifier.STATIC)
                && method.getType() instanceof VoidType
                && method.getParameters().isEmpty();
    }

    private static boolean hasJUnit5Signature(MethodDeclaration method) {
        return !method.getModifiers().contains(Modifier.PRIVATE)
                && !method.getModifiers().contains(Modifier.STATIC)
                && method.getType() instanceof VoidType;
    }

    private static boolean hasJUnit5FactorySignature(MethodDeclaration method) {
        return !method.getModifiers().contains(Modifier.PRIVATE)
                && !method.getModifiers().contains(Modifier.STATIC)
                && !(method.getType() instanceof VoidType);
    }

    private static boolean hasLifecycleSignature(MethodDeclaration method) {
        if (hasAnyAnnotation(method, unmodifiableSet("org.junit.Before", "org.junit.After"))) {
            return hasJUnit4Signature(method);
        }
        if (hasAnyAnnotation(method, unmodifiableSet("org.junit.BeforeClass", "org.junit.AfterClass"))) {
            return method.getModifiers().contains(Modifier.PUBLIC)
                    && method.getModifiers().contains(Modifier.STATIC)
                    && method.getType() instanceof VoidType
                    && method.getParameters().isEmpty();
        }
        boolean beforeOrAfterEach = hasAnyAnnotation(method, unmodifiableSet(
                "org.junit.jupiter.api.BeforeEach", "org.junit.jupiter.api.AfterEach"));
        return !method.getModifiers().contains(Modifier.PRIVATE)
                && (!beforeOrAfterEach || !method.getModifiers().contains(Modifier.STATIC))
                && method.getType() instanceof VoidType;
    }

    private static boolean hasAnyAnnotation(NodeWithAnnotations<?> node, Set<String> annotationNames) {
        for (String annotationName : annotationNames) {
            if (hasAnnotation(node, annotationName)) {
                return true;
            }
        }
        return false;
    }

    private static boolean hasAnnotation(NodeWithAnnotations<?> node, String qualifiedName) {
        for (AnnotationExpr annotation : node.getAnnotations()) {
            String usedName = annotation.getNameAsString();
            if (usedName.equals(qualifiedName)) {
                return true;
            }
            if (!usedName.contains(".") && usedName.equals(simpleName(qualifiedName))
                    && isImported((Node) node, qualifiedName)) {
                return true;
            }
        }
        return false;
    }

    private static boolean isImported(Node node, String qualifiedName) {
        CompilationUnit compilationUnit = compilationUnitOf(node);
        if (compilationUnit == null) {
            return false;
        }
        String packageName = qualifiedName.substring(0, qualifiedName.lastIndexOf('.'));
        for (ImportDeclaration importDeclaration : compilationUnit.getImports()) {
            if (importDeclaration.isStatic()) {
                continue;
            }
            String importedName = importDeclaration.getNameAsString();
            if ((!importDeclaration.isAsterisk() && importedName.equals(qualifiedName))
                    || (importDeclaration.isAsterisk() && importedName.equals(packageName))) {
                return true;
            }
        }
        return false;
    }

    private static CompilationUnit compilationUnitOf(Node node) {
        Node current = node;
        while (current != null) {
            if (current instanceof CompilationUnit) {
                return (CompilationUnit) current;
            }
            current = current.getParentNode().orElse(null);
        }
        return null;
    }

    private static String simpleName(String qualifiedName) {
        return qualifiedName.substring(qualifiedName.lastIndexOf('.') + 1);
    }

    private static Set<String> unmodifiableSet(String... values) {
        return Collections.unmodifiableSet(new HashSet<>(Arrays.asList(values)));
    }
}
