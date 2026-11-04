package service;

import com.google.common.collect.Lists;
import com.google.common.collect.Maps;
import com.google.common.collect.Sets;
import entity.MethodParameterContext;
import entity.RunContext;
import entity.ProductionClassContext;
import entity.ProductionContext;
import entity.ProductionDependencyMethodContext;
import entity.ProductionLLMContext;
import entity.ProductionMethodAstSummary;
import entity.ProductionMethodContext;
import entity.ProductionMethodLibraryEntry;
import entity.RefactoringLLMContext;
import entity.TestLLMContext;
import entity.TestSmellDetectionOutput;
import entity.TestSmellType;
import org.apache.commons.lang3.StringUtils;
import service.preprocess.ProductionContextExtractor;
import util.FileUtil;
import util.JsonUtil;

import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;

public class TestContextCollection {
    private static final String METHOD_LIBRARY_FILENAME = "_production_method_library.json";
    private final Map<String, ProductionMethodLibraryEntry> productionMethodLibrary = Maps.newLinkedHashMap();

    public List<RefactoringLLMContext> buildRefactoringLLMContextList(List<TestSmellDetectionOutput> llmRefactoringTestList) {
        productionMethodLibrary.clear();
        int detectedOutputCount = llmRefactoringTestList.size();
        Map<String, Long> ruleCounts = llmRefactoringTestList.stream()
                .collect(Collectors.groupingBy(
                        output -> output.getTestRefactorRule() == null
                                ? "UNASSIGNED"
                                : output.getTestRefactorRule().name(),
                        Collectors.counting()));
        long withOriginalCodeCount = llmRefactoringTestList.stream()
                .filter(x -> StringUtils.isNotBlank(x.getTestOriginalCode()))
                .count();
        System.out.println("[stats] detectedOutputs=" + detectedOutputCount);
        System.out.println("[stats] refactorRuleCounts=" + ruleCounts);
        System.out.println("[stats] outputsWithOriginalCode=" + withOriginalCodeCount);
        ProductionContextExtractor productionContextExtractor = new ProductionContextExtractor();

        List<RefactoringLLMContext> contexts = llmRefactoringTestList.stream()
                .filter(x -> StringUtils.isNotBlank(x.getTestOriginalCode()))
                .map(output -> {
                    List<String> smells = output.getTestSmellTypeList().stream()
                            .map(TestSmellType::getDetectorName).toList();
                    List<String> selectedSmells = safeList(output.getSelectedSmellTypeList()).stream()
                            .map(Enum::name).toList();
                    List<String> ignoredSmells = safeList(output.getIgnoredSmellTypeList()).stream()
                            .map(Enum::name).toList();
                    TestLLMContext testContext = TestLLMContext.builder()
                            .testModulePath(output.getModulePath())
                            .testFilename(originalTestFilename(output.getOriginalTestFilepath()))
                            .testFilePath(output.getOriginalTestFilepath())
                            .testRelativePath(output.getTestRelativePath())
                            .testPackageName(output.getTestPackageName())
                            .splitFilename(output.getSplitFilename())
                            .refactorRule(output.getTestRefactorRule() == null
                                    ? "UNASSIGNED"
                                    : output.getTestRefactorRule().name())
                            .testCodeStartLine(output.getTestCodeStartLine())
                            .testCodeEndLine(output.getTestCodeEndLine())
                            .testCode(output.getTestOriginalCode())
                            .codeComment("")
                            .smellList(smells)
                            .selectedSmells(selectedSmells)
                            .ignoredSmells(ignoredSmells)
                            .build();

                    ProductionContext productionContext = productionContextExtractor.buildProductionContext(output);
                    return RefactoringLLMContext.builder()
                            .schemaVersion("4.1")
                            .testContext(testContext)
                            .productionContext(buildProductionLLMContext(productionContext))
                            .build();
                })
                .filter(Objects::nonNull)
                .collect(Collectors.toList());
        System.out.println("[stats] finalJsonArrayElementCount=" + contexts.size());
        return contexts;
    }
    public void collectTestToJson(String projectName, List<RefactoringLLMContext> refactoringLLMContexts, RunContext runContext) {
        Map<String, List<RefactoringLLMContext>> map = Maps.newHashMap();
        Set<String> keys = Sets.newHashSet();
        for (RefactoringLLMContext refactoringLLMContext : refactoringLLMContexts) {
            keys.add(jsonGroupKey(refactoringLLMContext.getTestContext()));
        }
        for (RefactoringLLMContext refactoringLLMContext : refactoringLLMContexts) {
            String key = jsonGroupKey(refactoringLLMContext.getTestContext());
            if (keys.contains(key)) {
                map.computeIfAbsent(key, k -> Lists.newArrayList());
                map.get(key).add(refactoringLLMContext);
            }
        }
        int jsonArrayElementCount = map.values().stream().mapToInt(List::size).sum();
        System.out.println("[stats] jsonFileCount=" + map.size());
        System.out.println("[stats] jsonArrayElementCount=" + jsonArrayElementCount);
        Set<String> libraryDirs = Sets.newHashSet();
        map.forEach((key, value) -> {
            String moduleJsonDir = moduleJsonDir(runContext.getJsonDir(), key);
            if (libraryDirs.add(moduleJsonDir)) {
                collectProductionMethodLibraryToJson(projectName, moduleJsonDir);
            }
            collectTestLLMContextToJson(projectName, key, value, runContext.getJsonDir());
        });
    }

    private void collectProductionMethodLibraryToJson(String projectName, String jsonDir) {
        if (productionMethodLibrary.isEmpty()) {
            return;
        }
        String jsonString = JsonUtil.objectToJson(productionMethodLibrary);
        if (StringUtils.isBlank(jsonString)) {
            return;
        }
        String jsonfilePath = jsonDir + "/" + METHOD_LIBRARY_FILENAME;
        FileUtil.writeDataToFile(jsonfilePath, jsonString);
        System.out.println("[stats] productionMethodLibraryEntryCount=" + productionMethodLibrary.size());
    }

    public static void collectTestLLMContextToJson(String projectName, String focalClassName, List<RefactoringLLMContext> refactoringLLMContextList, String jsonDir) {
        String jsonString = JsonUtil.objectToJson(refactoringLLMContextList);
        if (StringUtils.isBlank(jsonString)) {
            return;
        }
        String jsonfilePath = jsonDir + "/" + focalClassName + ".json";

        FileUtil.writeDataToFile(jsonfilePath, jsonString);
    }

    String jsonGroupKey(TestLLMContext testContext) {
        String filename = FileUtil.getNameByFilepath(testContext.getTestFilename());
        String packageName = StringUtils.defaultString(testContext.getTestPackageName()).trim();
        String modulePath = normalizeModulePath(testContext.getTestModulePath());
        String classIdentity = StringUtils.isBlank(packageName)
                ? filename
                : packageName + "." + filename;
        if (StringUtils.isBlank(modulePath)) {
            return classIdentity;
        }
        return modulePath.replace("/", ".") + "." + classIdentity;
    }

    private String moduleJsonDir(String jsonDir, String groupKey) {
        int slashIndex = groupKey.lastIndexOf("/");
        if (slashIndex < 0) {
            return jsonDir;
        }
        return jsonDir + "/" + groupKey.substring(0, slashIndex);
    }

    private String normalizeModulePath(String modulePath) {
        if (StringUtils.isBlank(modulePath)) {
            return StringUtils.EMPTY;
        }
        return modulePath.replace("\\", "/");
    }

    private ProductionLLMContext buildProductionLLMContext(ProductionContext productionContext) {
        if (productionContext == null) {
            return ProductionLLMContext.builder()
                    .available(false)
                    .reason("production context is null")
                    .build();
        }

        ProductionClassContext focalClass = productionContext.getFocalClass();
        List<ProductionMethodContext> focalMethods = productionContext.getFocalMethods() == null
                ? Lists.newArrayList()
                : productionContext.getFocalMethods();
        List<String> focalMethodRefs = focalMethods.stream()
                .map(method -> registerProductionMethod(focalClass, method))
                .collect(Collectors.toList());

        return ProductionLLMContext.builder()
                .available(productionContext.isAvailable())
                .reason(productionContext.getReason())
                .productionClassPath(focalClass == null ? "" : focalClass.getPath())
                .productionPackageName(focalClass == null ? "" : focalClass.getPackageName())
                .productionClassName(focalClass == null ? "" : focalClass.getClassName())
                .productionImports(focalClass == null ? Lists.newArrayList() : focalClass.getImports())
                .productionFields(focalClass == null ? Lists.newArrayList() : focalClass.getFields())
                .methodLibraryPath(METHOD_LIBRARY_FILENAME)
                .focalMethodRefs(focalMethodRefs)
                .focalMethodNames(focalMethods.stream().map(ProductionMethodContext::getName).collect(Collectors.toList()))
                .focalMethodSignatures(focalMethods.stream().map(ProductionMethodContext::getSignature).collect(Collectors.toList()))
                .focalMethodInputs(focalMethods.stream()
                        .flatMap(method -> formatParameters(method.getParameters()).stream())
                        .distinct()
                        .collect(Collectors.toList()))
                .focalMethodOutputs(focalMethods.stream().map(ProductionMethodContext::getReturnType).distinct().collect(Collectors.toList()))
                .focalMethodThrownExceptions(focalMethods.stream()
                        .flatMap(method -> safeList(method.getThrownExceptions()).stream())
                        .distinct()
                        .collect(Collectors.toList()))
                .focalMethodCodeSnippets(Lists.newArrayList())
                .focalMethodSummaries(focalMethods.stream().map(ProductionMethodContext::getSummary).collect(Collectors.toList()))
                .directCallNames(Lists.newArrayList())
                .dependencyMethodSignatures(Lists.newArrayList())
                .dependencyMethodInputs(Lists.newArrayList())
                .dependencyMethodOutputs(Lists.newArrayList())
                .dependencyMethodSummaries(Lists.newArrayList())
                .astNodeTypeSummaries(Lists.newArrayList())
                .astMethodCalls(Lists.newArrayList())
                .astObjectCreations(Lists.newArrayList())
                .astControlFlow(Lists.newArrayList())
                .compactAstTrees(Lists.newArrayList())
                .build();
    }

    private String registerProductionMethod(ProductionClassContext focalClass, ProductionMethodContext method) {
        String refKey = buildMethodRefKey(focalClass, method);
        if (productionMethodLibrary.containsKey(refKey)) {
            return refKey;
        }
        ProductionMethodAstSummary astSummary = method.getAstSummary();
        List<ProductionDependencyMethodContext> dependencyMethods = safeList(method.getDependencyMethods());
        ProductionMethodLibraryEntry entry = ProductionMethodLibraryEntry.builder()
                .refKey(refKey)
                .productionClassPath(focalClass == null ? "" : focalClass.getPath())
                .productionPackageName(focalClass == null ? "" : focalClass.getPackageName())
                .productionClassName(focalClass == null ? "" : focalClass.getClassName())
                .focalMethodName(method.getName())
                .focalMethodSignature(method.getSignature())
                .focalMethodInputs(formatParameters(method.getParameters()))
                .focalMethodOutput(method.getReturnType())
                .focalMethodThrownExceptions(safeList(method.getThrownExceptions()))
                .focalMethodCodeSnippet(method.getCode())
                .focalMethodSummary(method.getSummary())
                .directCallNames(safeList(method.getDirectCalls()))
                .dependencyMethodSignatures(dependencyMethods.stream()
                        .map(ProductionDependencyMethodContext::getSignature)
                        .distinct()
                        .collect(Collectors.toList()))
                .dependencyMethodInputs(dependencyMethods.stream()
                        .flatMap(dependency -> formatParameters(dependency.getParameters()).stream())
                        .distinct()
                        .collect(Collectors.toList()))
                .dependencyMethodOutputs(dependencyMethods.stream()
                        .map(ProductionDependencyMethodContext::getReturnType)
                        .distinct()
                        .collect(Collectors.toList()))
                .dependencyMethodSummaries(dependencyMethods.stream()
                        .map(ProductionDependencyMethodContext::getSummary)
                        .collect(Collectors.toList()))
                .astNodeTypeSummary(astSummary == null ? "" : formatNodeTypeSummary(astSummary))
                .astMethodCalls(astSummary == null ? Lists.newArrayList() : safeList(astSummary.getMethodCalls()))
                .astObjectCreations(astSummary == null ? Lists.newArrayList() : safeList(astSummary.getObjectCreations()))
                .astControlFlow(astSummary == null ? Lists.newArrayList() : safeList(astSummary.getControlFlow()))
                .compactAstTree(astSummary == null ? null : astSummary.getCompactTree())
                .build();
        productionMethodLibrary.put(refKey, entry);
        return refKey;
    }

    private String buildMethodRefKey(ProductionClassContext focalClass, ProductionMethodContext method) {
        String classKey = focalClass == null
                ? ""
                : String.join("#",
                        nullToEmpty(focalClass.getPath()),
                        nullToEmpty(focalClass.getPackageName()),
                        nullToEmpty(focalClass.getClassName()));
        return classKey + "::" + nullToEmpty(method.getSignature());
    }

    private String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private List<String> formatParameters(List<MethodParameterContext> parameters) {
        return safeList(parameters).stream()
                .map(parameter -> parameter.getType() + " " + parameter.getName())
                .collect(Collectors.toList());
    }

    private String formatNodeTypeSummary(ProductionMethodAstSummary astSummary) {
        if (astSummary.getNodeTypeCounts() == null || astSummary.getNodeTypeCounts().isEmpty()) {
            return "";
        }
        return astSummary.getNodeTypeCounts().entrySet().stream()
                .map(entry -> entry.getKey() + "=" + entry.getValue())
                .collect(Collectors.joining(", "));
    }

    private <T> List<T> safeList(List<T> values) {
        return values == null ? Lists.newArrayList() : values;
    }

    private String originalTestFilename(String splitFilepath) {
        return FileUtil.getFileName(splitFilepath).replaceFirst("__\\d+(?=\\.java$)", "");
    }

}
