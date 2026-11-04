package testsmell;

/** A failure tied to its original detector-input record. */
public final class FailureRecord {
    public enum Stage { INPUT_VALIDATION, JAVA_ANALYSIS, RULE_ANALYSIS }

    private final long inputRecordNumber;
    private final long inputStartLine;
    private final String testFilePath;
    private final Stage stage;
    private final String reason;

    public FailureRecord(long inputRecordNumber, long inputStartLine, String testFilePath, Stage stage, String reason) {
        this.inputRecordNumber = inputRecordNumber;
        this.inputStartLine = inputStartLine;
        this.testFilePath = testFilePath;
        this.stage = stage;
        this.reason = reason;
    }

    public String[] toCsvRow() {
        return new String[]{String.valueOf(inputRecordNumber), testFilePath, reason,
                String.valueOf(inputStartLine), stage.name()};
    }
}
