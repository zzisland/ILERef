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
public class ProductionDependencyMethodContext {
    /** 被测方法直接调用到的同类辅助方法名。 */
    private String name;

    /** 辅助方法签名摘要。 */
    private String signature;

    /** 参数列表，后续可用于 prompt 中说明输入。 */
    private List<MethodParameterContext> parameters;

    /** 返回类型，后续可用于 prompt 中说明输出。 */
    private String returnType;

    /** 简短规则摘要，不依赖 LLM。 */
    private String summary;
}
