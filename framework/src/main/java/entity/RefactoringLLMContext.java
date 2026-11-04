package entity;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * 一条待重构测试方法的完整上下文。
 *
 * <p>它负责组合两类上下文：
 * testContext 只描述测试代码本身；
 * productionContext 只描述测试代码触达的被测生产代码。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class RefactoringLLMContext {
    
    private String schemaVersion;//结构版本，方便后续 JSON 格式升级时兼容旧数据

    private TestLLMContext testContext;//测试代码上下文

    private ProductionLLMContext productionContext;//被测生产代码上下文
}
