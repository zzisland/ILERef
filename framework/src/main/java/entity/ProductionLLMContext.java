package entity;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;

/**
 * 被测生产代码上下文展示实体。
 *
 * <p>这个类和 TestLLMContext 分开：TestLLMContext 只描述测试代码本身，
 * ProductionLLMContext 专门描述测试代码所触达的被测类、被测方法和依赖方法。
 *
 * <p>当前它先作为“字段结构说明”和后续 prompt/RAG 可读上下文的承载格式。
 * 现有抽取逻辑仍然使用 ProductionContext / ProductionMethodContext 等结构化实体，
 * 后续如果要调整 JSON 输出，可以把 TestLLMContext 和 ProductionLLMContext
 * 组合成一个更上层的 RefactoringContext。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ProductionLLMContext {

    private boolean available;//是否成功抽取到被测生产代码上下文
   
    private String reason;// 抽取失败或没有匹配到被测方法时的说明

    private String productionClassPath;//被测生产类源码路径

    private String productionPackageName;//被测生产类包名

    private String productionClassName;//被测生产类类名

    private List<String> productionImports;//被测生产类 import 列表

    private List<String> productionFields;//被测生产类字段声明

    private List<String> focalMethodNames;//测试方法直接触达并在生产类中匹配到的方法名列表

    private List<String> focalMethodSignatures;//测试方法直接触达并在生产类中匹配到的方法签名列表

    private List<String> focalMethodInputs;//被测方法输入参数摘要，例如 "String name"

    private List<String> focalMethodOutputs;//被测方法返回类型摘要

    private List<String> focalMethodThrownExceptions;//被测方法 throws 声明摘要

    private List<String> focalMethodCodeSnippets;//被测方法源码片段

    private List<String> focalMethodSummaries;//规则生成的被测方法摘要，不依赖 LLM

    private List<String> directCallNames;//被测方法内部直接调用到的方法名

    private List<String> dependencyMethodSignatures;//能在同一个生产类里回查到的直接依赖方法签名

    private List<String> dependencyMethodInputs;//依赖方法输入参数摘

    private List<String> dependencyMethodOutputs;//依赖方法返回类型摘要

    private List<String> dependencyMethodSummaries;//依赖方法规则摘要

    private List<String> astNodeTypeSummaries;//被测方法 AST 节点类型统计摘要

    private List<String> astMethodCalls;//被测方法 AST 中出现的方法调用名

    private List<String> astObjectCreations;//被测方法 AST 中出现的对象创建类型

    private List<String> astControlFlow;//被测方法 AST 中出现的控制流结构，例如 if、for、try

    private List<Map<String, Object>> compactAstTrees;//被测方法紧凑 AST 树，默认只保留少量层级，避免 prompt 过长
    private String methodLibraryPath;
    private List<String> focalMethodRefs;
}
