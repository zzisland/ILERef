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
public class TestLLMContext {
    private String testCode;
    private String codeComment;// 测试代码注释，目前为空，没有真正提取注释
    private String testModulePath;
    private String testFilename;
    private String testFilePath;
    private String testRelativePath;
    private String testPackageName;
    private String splitFilename;
    private String refactorRule;
    private int testCodeStartLine;
    private int testCodeEndLine;
    private List<String> smellList;
    private List<String> selectedSmells;
    private List<String> ignoredSmells;
}
