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
public class ProductionContext {
    /** 是否成功从生产类文件中抽取上下文。 */
    private boolean available;

    /** 失败原因或保守跳过原因，方便后续排查数据质量。 */
    private String reason;

    /** 被测生产类上下文。 */
    private ProductionClassContext focalClass;

    /** 测试方法直接调用到、且能在生产类中匹配到的被测方法列表。 */
    private List<ProductionMethodContext> focalMethods;
}
