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
public class ProductionMethodLibraryEntry {
    private String refKey;

    private String productionClassPath;
    private String productionPackageName;
    private String productionClassName;

    private String focalMethodName;
    private String focalMethodSignature;
    private List<String> focalMethodInputs;
    private String focalMethodOutput;
    private List<String> focalMethodThrownExceptions;
    private String focalMethodCodeSnippet;
    private String focalMethodSummary;

    private List<String> directCallNames;
    private List<String> dependencyMethodSignatures;
    private List<String> dependencyMethodInputs;
    private List<String> dependencyMethodOutputs;
    private List<String> dependencyMethodSummaries;

    private String astNodeTypeSummary;
    private List<String> astMethodCalls;
    private List<String> astObjectCreations;
    private List<String> astControlFlow;
    private Map<String, Object> compactAstTree;
}
