package service.preprocess;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.stmt.DoStmt;
import com.github.javaparser.ast.stmt.ForEachStmt;
import com.github.javaparser.ast.stmt.ForStmt;
import com.github.javaparser.ast.stmt.IfStmt;
import com.github.javaparser.ast.stmt.SwitchStmt;
import com.github.javaparser.ast.stmt.TryStmt;
import com.github.javaparser.ast.stmt.WhileStmt;
import com.google.common.collect.Lists;
import com.google.common.collect.Maps;
import entity.MethodParameterContext;
import entity.ProductionClassContext;
import entity.ProductionContext;
import entity.ProductionDependencyMethodContext;
import entity.ProductionMethodAstSummary;
import entity.ProductionMethodContext;
import entity.TestSmellDetectionOutput;
import org.antlr.v4.runtime.ParserRuleContext;
import org.apache.commons.lang3.StringUtils;

import java.io.File;
import java.io.IOException;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;

public class ProductionContextExtractor {
    private static final int DEFAULT_AST_DEPTH = 3;
    private static final int MAX_DEPENDENCY_METHODS = 5;

    /**
     * 从测试检测输出中抽取生产代码上下文。
     *
     * <p>第一版采用保守策略：只在 classFilepath 指向的生产类中匹配测试方法直接调用到的同名方法。
     * 不做跨类类型解析，也不递归展开依赖，避免上下文过大或错误绑定。</p>
     */
    public ProductionContext buildProductionContext(TestSmellDetectionOutput output) {
        if (output == null || StringUtils.isBlank(output.getClassFilepath())) {
            return unavailable("empty production class filepath");
        }

        File productionFile = new File(output.getClassFilepath());
        if (!productionFile.exists() || !productionFile.isFile()) {
            return unavailable("production class file not found: " + output.getClassFilepath());
        }

        CompilationUnit compilationUnit;
        try {
            compilationUnit = StaticJavaParser.parse(productionFile);
        } catch (IOException | RuntimeException e) {
            return unavailable("failed to parse production class: " + e.getMessage());
        }
        Optional<ClassOrInterfaceDeclaration> targetClass = resolveTargetClass(compilationUnit, productionFile);

        List<String> invokedMethodNames = fetchTestInvokedMethodNames(output.getMethodCtx());
        if (invokedMethodNames.isEmpty()) {
            return ProductionContext.builder()
                    .available(true)
                    .reason("no method invocation found in test method")
                    .focalClass(buildClassContext(compilationUnit, targetClass, output.getClassFilepath()))
                    .focalMethods(Lists.newArrayList())
                    .build();
        }

        Map<String, List<MethodDeclaration>> methodsByName = buildMethodsByName(targetClass);
        List<ProductionMethodContext> focalMethods = Lists.newArrayList();
        for (String methodName : invokedMethodNames) {
            List<MethodDeclaration> matchedMethods = methodsByName.get(methodName);
            if (matchedMethods == null || matchedMethods.isEmpty()) {
                continue;
            }
            for (MethodDeclaration methodDeclaration : matchedMethods) {
                focalMethods.add(buildMethodContext(methodDeclaration, methodsByName));
            }
        }

        return ProductionContext.builder()
                .available(true)
                .reason(focalMethods.isEmpty() ? "no invoked method matched in production class" : "")
                .focalClass(buildClassContext(compilationUnit, targetClass, output.getClassFilepath()))
                .focalMethods(focalMethods)
                .build();
    }

    private ProductionContext unavailable(String reason) {
        return ProductionContext.builder()
                .available(false)
                .reason(reason)
                .focalClass(null)
                .focalMethods(Lists.newArrayList())
                .build();
    }

    private List<String> fetchTestInvokedMethodNames(ParserRuleContext methodCtx) {
        if (methodCtx == null) {
            return Lists.newArrayList();
        }
        MethodInvocationExtractor extractor = new MethodInvocationExtractor();
        extractor.visit(methodCtx);
        return distinct(extractor.getMethodNames());
    }

    private Map<String, List<MethodDeclaration>> buildMethodsByName(
            Optional<ClassOrInterfaceDeclaration> targetClass) {
        Map<String, List<MethodDeclaration>> map = Maps.newHashMap();
        if (targetClass.isEmpty()) {
            return map;
        }
        for (MethodDeclaration methodDeclaration : targetClass.get().getMethods()) {
            map.computeIfAbsent(methodDeclaration.getNameAsString(), k -> Lists.newArrayList())
                    .add(methodDeclaration);
        }
        return map;
    }

    private Optional<ClassOrInterfaceDeclaration> resolveTargetClass(
            CompilationUnit compilationUnit,
            File productionFile) {
        String filename = productionFile.getName();
        String expectedClassName = filename.endsWith(".java")
                ? filename.substring(0, filename.length() - ".java".length())
                : filename;
        Optional<ClassOrInterfaceDeclaration> namedClass = compilationUnit.getClassByName(expectedClassName);
        if (namedClass.isPresent()) {
            return namedClass;
        }
        return compilationUnit.getTypes().stream()
                .filter(type -> type instanceof ClassOrInterfaceDeclaration)
                .map(type -> (ClassOrInterfaceDeclaration) type)
                .findFirst();
    }

    private ProductionClassContext buildClassContext(
            CompilationUnit compilationUnit,
            Optional<ClassOrInterfaceDeclaration> classDeclaration,
            String classFilepath) {
        return ProductionClassContext.builder()
                .path(classFilepath)
                .packageName(compilationUnit.getPackageDeclaration()
                        .map(packageDeclaration -> packageDeclaration.getName().asString())
                        .orElse(""))
                .className(classDeclaration.map(node -> node.getName().asString()).orElse(""))
                .imports(compilationUnit.getImports().stream()
                        .map(importDeclaration -> importDeclaration.getNameAsString())
                        .collect(Collectors.toList()))
                .fields(classDeclaration.stream()
                        .flatMap(node -> node.getFields().stream())
                        .map(FieldDeclaration::toString)
                        .collect(Collectors.toList()))
                .build();
    }

    private ProductionMethodContext buildMethodContext(
            MethodDeclaration methodDeclaration,
            Map<String, List<MethodDeclaration>> methodsByName) {
        List<String> directCalls = distinct(methodDeclaration.findAll(MethodCallExpr.class).stream()
                .map(callExpr -> callExpr.getName().asString())
                .collect(Collectors.toList()));

        return ProductionMethodContext.builder()
                .name(methodDeclaration.getNameAsString())
                .signature(buildSignature(methodDeclaration))
                .parameters(buildParameters(methodDeclaration))
                .returnType(methodDeclaration.getType().asString())
                .thrownExceptions(methodDeclaration.getThrownExceptions().stream()
                        .map(type -> type.asString())
                        .collect(Collectors.toList()))
                .modifiers(methodDeclaration.getModifiers().stream()
                        .map(modifier -> modifier.getKeyword().asString())
                        .collect(Collectors.toList()))
                .code(methodDeclaration.toString())
                .summary(buildMethodSummary(methodDeclaration, directCalls))
                .astSummary(buildAstSummary(methodDeclaration))
                .directCalls(directCalls)
                .dependencyMethods(buildDependencyMethods(methodDeclaration, directCalls, methodsByName))
                .build();
    }

    private String buildSignature(MethodDeclaration methodDeclaration) {
        // JavaParser 已经能稳定生成包含修饰符、参数名和 throws 的声明字符串。
        return methodDeclaration.getDeclarationAsString(true, true, true);
    }

    private List<MethodParameterContext> buildParameters(MethodDeclaration methodDeclaration) {
        return methodDeclaration.getParameters().stream()
                .map(parameter -> MethodParameterContext.builder()
                        .type(parameter.getType().asString())
                        .name(parameter.getNameAsString())
                        .build())
                .collect(Collectors.toList());
    }

    private String buildMethodSummary(MethodDeclaration methodDeclaration, List<String> directCalls) {
        String parameterSummary = methodDeclaration.getParameters().stream()
                .map(parameter -> parameter.getType().asString() + " " + parameter.getNameAsString())
                .collect(Collectors.joining(", "));
        String callSummary = directCalls.isEmpty() ? "no direct method calls" : "calls " + String.join(", ", directCalls);
        return methodDeclaration.getNameAsString() + "(" + parameterSummary + ") -> "
                + methodDeclaration.getType().asString() + "; " + callSummary;
    }

    private ProductionMethodAstSummary buildAstSummary(MethodDeclaration methodDeclaration) {
        Map<String, Integer> nodeTypeCounts = Maps.newLinkedHashMap();
        collectNodeTypeCounts(methodDeclaration, nodeTypeCounts);

        List<String> methodCalls = distinct(methodDeclaration.findAll(MethodCallExpr.class).stream()
                .map(callExpr -> callExpr.getName().asString())
                .collect(Collectors.toList()));
        List<String> objectCreations = distinct(methodDeclaration.findAll(ObjectCreationExpr.class).stream()
                .map(expr -> expr.getType().asString())
                .collect(Collectors.toList()));
        List<String> controlFlow = collectControlFlow(methodDeclaration);

        return ProductionMethodAstSummary.builder()
                .nodeTypeCounts(nodeTypeCounts)
                .methodCalls(methodCalls)
                .objectCreations(objectCreations)
                .controlFlow(controlFlow)
                .compactTree(buildCompactTree(methodDeclaration, 0, DEFAULT_AST_DEPTH))
                .build();
    }

    private void collectNodeTypeCounts(Node node, Map<String, Integer> nodeTypeCounts) {
        String nodeType = node.getClass().getSimpleName();
        nodeTypeCounts.put(nodeType, nodeTypeCounts.getOrDefault(nodeType, 0) + 1);
        for (Node child : node.getChildNodes()) {
            collectNodeTypeCounts(child, nodeTypeCounts);
        }
    }

    private List<String> collectControlFlow(MethodDeclaration methodDeclaration) {
        List<String> controlFlow = Lists.newArrayList();
        if (!methodDeclaration.findAll(IfStmt.class).isEmpty()) {
            controlFlow.add("if");
        }
        if (!methodDeclaration.findAll(ForStmt.class).isEmpty()) {
            controlFlow.add("for");
        }
        if (!methodDeclaration.findAll(ForEachStmt.class).isEmpty()) {
            controlFlow.add("foreach");
        }
        if (!methodDeclaration.findAll(WhileStmt.class).isEmpty()) {
            controlFlow.add("while");
        }
        if (!methodDeclaration.findAll(DoStmt.class).isEmpty()) {
            controlFlow.add("do");
        }
        if (!methodDeclaration.findAll(TryStmt.class).isEmpty()) {
            controlFlow.add("try");
        }
        if (!methodDeclaration.findAll(SwitchStmt.class).isEmpty()) {
            controlFlow.add("switch");
        }
        return controlFlow;
    }

    private Map<String, Object> buildCompactTree(Node node, int depth, int maxDepth) {
        Map<String, Object> result = Maps.newLinkedHashMap();
        result.put("type", node.getClass().getSimpleName());

        String label = fetchNodeLabel(node);
        if (StringUtils.isNotBlank(label)) {
            result.put("label", label);
        }

        if (depth >= maxDepth) {
            result.put("truncatedChildCount", node.getChildNodes().size());
            return result;
        }

        List<Map<String, Object>> children = Lists.newArrayList();
        for (Node child : node.getChildNodes()) {
            children.add(buildCompactTree(child, depth + 1, maxDepth));
        }
        if (!children.isEmpty()) {
            result.put("children", children);
        }
        return result;
    }

    private String fetchNodeLabel(Node node) {
        if (node instanceof MethodDeclaration) {
            MethodDeclaration methodDeclaration = (MethodDeclaration) node;
            return methodDeclaration.getNameAsString();
        }
        if (node instanceof MethodCallExpr) {
            MethodCallExpr methodCallExpr = (MethodCallExpr) node;
            return methodCallExpr.getNameAsString();
        }
        if (node instanceof ObjectCreationExpr) {
            ObjectCreationExpr objectCreationExpr = (ObjectCreationExpr) node;
            return objectCreationExpr.getType().asString();
        }
        return "";
    }

    private List<ProductionDependencyMethodContext> buildDependencyMethods(
            MethodDeclaration focalMethod,
            List<String> directCalls,
            Map<String, List<MethodDeclaration>> methodsByName) {
        List<ProductionDependencyMethodContext> dependencies = Lists.newArrayList();
        for (String callName : directCalls) {
            if (dependencies.size() >= MAX_DEPENDENCY_METHODS) {
                break;
            }
            if (StringUtils.equals(callName, focalMethod.getNameAsString())) {
                continue;
            }
            List<MethodDeclaration> matchedMethods = methodsByName.get(callName);
            if (matchedMethods == null || matchedMethods.isEmpty()) {
                continue;
            }
            MethodDeclaration dependency = matchedMethods.get(0);
            dependencies.add(ProductionDependencyMethodContext.builder()
                    .name(dependency.getNameAsString())
                    .signature(buildSignature(dependency))
                    .parameters(buildParameters(dependency))
                    .returnType(dependency.getType().asString())
                    .summary(buildMethodSummary(dependency, distinct(dependency.findAll(MethodCallExpr.class).stream()
                            .map(callExpr -> callExpr.getName().asString())
                            .collect(Collectors.toList()))))
                    .build());
        }
        return dependencies;
    }

    private List<String> distinct(List<String> values) {
        Set<String> result = new LinkedHashSet<>();
        for (String value : values) {
            if (StringUtils.isNotBlank(value)) {
                result.add(value);
            }
        }
        return Lists.newArrayList(result);
    }
}
