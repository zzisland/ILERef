package testsmell;

import java.io.PrintStream;

/** Explicit counters and invariants for one CLI detector run. */
public final class RunStatistics {
    public int inputRowsTotal;
    public int inputRowsParsed;
    public int malformedInputRows;
    public int analysisAttemptedRows;
    public int analysisSucceededRows;
    public int analysisFailedRows;
    public int ruleAnalysisFailureRecords;
    public int outputRows;
    public int productionFileAvailableRows;
    public int productionFileMissingRows;

    public int failureRecordsTotal() {
        return malformedInputRows + analysisFailedRows + ruleAnalysisFailureRecords;
    }

    public void validate() {
        if (inputRowsTotal != inputRowsParsed + malformedInputRows)
            throw new IllegalStateException("input row accounting invariant violated");
        if (analysisAttemptedRows != analysisSucceededRows + analysisFailedRows)
            throw new IllegalStateException("analysis accounting invariant violated");
        if (inputRowsParsed != analysisAttemptedRows)
            throw new IllegalStateException("parsed rows must all be attempted");
        if (productionFileAvailableRows + productionFileMissingRows != inputRowsParsed)
            throw new IllegalStateException("production-file accounting invariant violated");
    }

    public void printTo(PrintStream out) {
        validate();
        out.println("[stats] input_rows_total=" + inputRowsTotal);
        out.println("[stats] input_rows_parsed=" + inputRowsParsed);
        out.println("[stats] malformed_input_rows=" + malformedInputRows);
        out.println("[stats] analysis_attempted_rows=" + analysisAttemptedRows);
        out.println("[stats] analysis_succeeded_rows=" + analysisSucceededRows);
        out.println("[stats] analysis_failed_rows=" + analysisFailedRows);
        out.println("[stats] rule_analysis_failure_records=" + ruleAnalysisFailureRecords);
        out.println("[stats] output_rows=" + outputRows);
        out.println("[stats] failure_records_total=" + failureRecordsTotal());
        out.println("[stats] production_file_available_rows=" + productionFileAvailableRows);
        out.println("[stats] production_file_missing_rows=" + productionFileMissingRows);
    }
}
