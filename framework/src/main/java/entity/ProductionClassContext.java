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
public class ProductionClassContext {
    /** 生产类源码路径。 */
    private String path;

    /** package 声明。 */
    private String packageName;

    /** 类名。 */
    private String className;

    /** import 列表。 */
    private List<String> imports;

    /** 字段声明摘要，第一版只保留字段声明文本。 */
    private List<String> fields;
}
