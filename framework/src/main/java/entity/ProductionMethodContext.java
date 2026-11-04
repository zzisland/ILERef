package entity;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ProductionMethodContext {
    /** 被测试代码直接触达的生产方法名。 */
    private String name;

    /** 方法签名，例如 public Checkpoint deserializeRecord(ConsumerRecord<byte[], byte[]> record)。 */
    private String signature;

    /** 输入参数列表。 */
    private List<MethodParameterContext> parameters;

    /** 返回类型。 */
    private String returnType;

    /** throws 声明。 */
    private List<String> thrownExceptions;

    /** public/static/final 等修饰符。 */
    private List<String> modifiers;

    /** 方法源码片段，后续是否进入 prompt 可以再控制。 */
    private String code;

    /** 规则生成的简短摘要，先不依赖 LLM。 */
    private String summary;

    /** 生产方法 AST 摘要。 */
    private ProductionMethodAstSummary astSummary;

    /** 生产方法内部直接调用到的方法名。 */
    private List<String> directCalls;

    /** 在同一个生产类里能回查到的直接依赖方法签名。 */
    private List<ProductionDependencyMethodContext> dependencyMethods;
}
