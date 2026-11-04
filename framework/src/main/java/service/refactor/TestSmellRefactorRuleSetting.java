package service.refactor;

import entity.TestSmellDetectionOutput;
import entity.TestSmellType;
import org.apache.commons.collections.CollectionUtils;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Selects which detected smells are in scope for the current experiment.
 *
 * <p>The historical class name and {@link #setRefactoringRule(List)} entry point are retained for
 * compatibility. This class no longer decides whether Python calls an LLM, skips a smell, or
 * removes a test.</p>
 */
public class TestSmellRefactorRuleSetting {
    private static final Path DEFAULT_SCOPE_CONFIG = Path.of("config", "refactoring_scope.yaml");

    private final Path scopeConfigPath;

    public TestSmellRefactorRuleSetting() {
        this(resolveScopeConfigPath());
    }

    TestSmellRefactorRuleSetting(Path scopeConfigPath) {
        this.scopeConfigPath = scopeConfigPath;
    }

    public List<TestSmellDetectionOutput> selectSmells(
            List<TestSmellDetectionOutput> testSmellList) {
        Map<String, Boolean> enabledSmells = loadEnabledSmells(scopeConfigPath);
        List<TestSmellDetectionOutput> detectedTests = testSmellList.stream()
                .filter(output -> CollectionUtils.isNotEmpty(output.getTestSmellTypeList()))
                .toList();

        for (TestSmellDetectionOutput output : detectedTests) {
            List<TestSmellType> selected = new ArrayList<>();
            List<TestSmellType> ignored = new ArrayList<>();
            for (TestSmellType smell : output.getTestSmellTypeList()) {
                if (enabledSmells.getOrDefault(smell.name(), false)) {
                    selected.add(smell);
                } else {
                    ignored.add(smell);
                }
            }
            output.setSelectedSmellTypeList(selected);
            output.setIgnoredSmellTypeList(ignored);
        }
        return detectedTests;
    }

    /** Compatibility alias for the original pipeline entry point. */
    public List<TestSmellDetectionOutput> setRefactoringRule(
            List<TestSmellDetectionOutput> testSmellList) {
        return selectSmells(testSmellList);
    }

    static Map<String, Boolean> loadEnabledSmells(Path configPath) {
        if (!Files.isRegularFile(configPath)) {
            throw new IllegalStateException(
                    "Refactoring scope config does not exist: " + configPath.toAbsolutePath());
        }
        Map<String, Boolean> enabledSmells = new LinkedHashMap<>();
        boolean inEnabledSmells = false;
        try {
            for (String rawLine : Files.readAllLines(configPath)) {
                String withoutComment = rawLine.replaceFirst("\\s+#.*$", "");
                String trimmed = withoutComment.trim();
                if (trimmed.isEmpty()) {
                    continue;
                }
                if (!Character.isWhitespace(withoutComment.charAt(0))) {
                    inEnabledSmells = "enabled_smells:".equals(trimmed);
                    continue;
                }
                if (!inEnabledSmells) {
                    continue;
                }
                String[] parts = trimmed.split(":", 2);
                if (parts.length != 2) {
                    throw new IllegalStateException(
                            "Invalid enabled_smells entry in " + configPath + ": " + rawLine);
                }
                String smell = parts[0].trim();
                String value = parts[1].trim();
                if (!"true".equalsIgnoreCase(value) && !"false".equalsIgnoreCase(value)) {
                    throw new IllegalStateException(
                            "Expected true or false for " + smell + " in " + configPath);
                }
                enabledSmells.put(smell, Boolean.parseBoolean(value));
            }
        } catch (IOException exception) {
            throw new IllegalStateException(
                    "Cannot read refactoring scope config: " + configPath, exception);
        }
        if (enabledSmells.isEmpty()) {
            throw new IllegalStateException(
                    "No enabled_smells entries found in " + configPath.toAbsolutePath());
        }
        return enabledSmells;
    }

    private static Path resolveScopeConfigPath() {
        String configuredPath = System.getProperty("utrefactor.scopeConfig");
        if (configuredPath == null || configuredPath.isBlank()) {
            configuredPath = System.getenv("UTREFACTOR_SCOPE_CONFIG");
        }
        return configuredPath == null || configuredPath.isBlank()
                ? DEFAULT_SCOPE_CONFIG
                : Path.of(configuredPath);
    }
}
