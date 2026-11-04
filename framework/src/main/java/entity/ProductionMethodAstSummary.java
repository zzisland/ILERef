package entity;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ProductionMethodAstSummary {
    /** JavaParser 节点类型计数，用来粗粒度描述方法结构。 */
    private Map<String, Integer> nodeTypeCounts;

    /** 方法调用名列表，保留顺序并去重。 */
    private List<String> methodCalls;

    /** 对象创建类型列表，保留顺序并去重。 */
    private List<String> objectCreations;

    /** 控制流节点，例如 if、for、while、try、switch。 */
    private List<String> controlFlow;

    /** 紧凑 AST 树，默认只保留 3 层，避免上下文过长。 */
    private Map<String, Object> compactTree;
}
