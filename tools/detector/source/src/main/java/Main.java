import com.opencsv.CSVWriter;
import testsmell.AbstractSmell;
import testsmell.FailureRecord;
import testsmell.InputCsvReader;
import testsmell.ResultsWriter;
import testsmell.RunStatistics;
import testsmell.TestFile;
import testsmell.TestSmellDetector;
import thresholds.DefaultThresholds;

import java.io.File;
import java.io.IOException;
import java.io.PrintStream;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.text.DateFormat;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class Main {
    public static void main(String[] args) throws IOException {
        if (args == null || args.length == 0) {
            System.out.println("Please provide the file containing the paths to the collection of test files");
            return;
        }
        File inputFile = new File(args[0]);
        if (!inputFile.isFile()) {
            System.out.println("Please provide a valid file containing the paths to the collection of test files");
            return;
        }

        run(inputFile.toPath(), ResultsWriter.createResultsWriter(),
                new TestSmellDetector(new DefaultThresholds()), System.out, System.err);
    }

    /** Package-independent seam used by focused workflow tests. */
    public static RunStatistics run(Path inputPath, ResultsWriter resultsWriter,
                                    TestSmellDetector detector, PrintStream out, PrintStream err) throws IOException {
        InputCsvReader.InputData inputData = InputCsvReader.read(inputPath);
        List<String> smellNames = detector.getTestSmellNames();
        List<String> columnNames = new ArrayList<>(smellNames);
        columnNames.add(0, "App");
        columnNames.add(1, "TestClass");
        columnNames.add(2, "TestFilePath");
        columnNames.add(3, "ProductionFilePath");
        columnNames.add(4, "RelativeTestFilePath");
        columnNames.add(5, "RelativeProductionFilePath");
        columnNames.add(6, "NumberOfMethods");
        resultsWriter.writeColumnName(columnNames);

        Map<String, Integer> smellCounts = new LinkedHashMap<>();
        for (String smellName : smellNames) smellCounts.put(smellName, 0);

        RunStatistics statistics = new RunStatistics();
        List<FailureRecord> failures = new ArrayList<>();
        DateFormat dateFormat = new SimpleDateFormat("yyyy/MM/dd HH:mm:ss");
        statistics.inputRowsTotal = inputData.getRecords().size();

        for (InputCsvReader.InputRecord record : inputData.getRecords()) {
            if (!record.isValid()) {
                statistics.malformedInputRows++;
                String[] values = record.getValues();
                String path = values.length >= 2 ? values[1] : "";
                failures.add(new FailureRecord(record.getRecordNumber(), record.getStartLine(), path,
                        FailureRecord.Stage.INPUT_VALIDATION,
                        "Invalid CSV row: expected non-empty App and TestFilePath"));
                continue;
            }

            statistics.inputRowsParsed++;
            statistics.analysisAttemptedRows++;
            TestFile file = record.toTestFile();
            if (file.getHasProductionFile()) statistics.productionFileAvailableRows++;
            else statistics.productionFileMissingRows++;
            out.println(dateFormat.format(new Date()) + " Processing: " + file.getTestFilePath());

            try {
                TestFile analyzed = detector.detectSmells(file);
                statistics.analysisSucceededRows++;
                resultsWriter.writeLine(successRow(file, analyzed, smellCounts));
                for (String[] ruleFailure : analyzed.getRuleFailures()) {
                    statistics.ruleAnalysisFailureRecords++;
                    failures.add(new FailureRecord(record.getRecordNumber(), record.getStartLine(),
                            file.getTestFilePath(), FailureRecord.Stage.RULE_ANALYSIS,
                            "smell=" + ruleFailure[0] + ": " + ruleFailure[1]));
                }
            } catch (Exception exception) {
                statistics.analysisFailedRows++;
                failures.add(new FailureRecord(record.getRecordNumber(), record.getStartLine(),
                        file.getTestFilePath(), FailureRecord.Stage.JAVA_ANALYSIS,
                        exception.getClass().getSimpleName() + ": " + String.valueOf(exception.getMessage())));
                err.println("[analysis-failure] input_record_number=" + record.getRecordNumber()
                        + ", input_start_line=" + record.getStartLine() + ", test=" + file.getTestFilePath()
                        + ", reason=" + exception.getClass().getSimpleName() + ": " + exception.getMessage());
                writeFailedRow(resultsWriter, file, smellNames.size());
            }
            statistics.outputRows++;
        }

        writeFailures(resultsWriter.getOutputFile(), failures);
        statistics.validate();
        statistics.printTo(out);
        out.println("[stats] smell_counts=" + smellCounts);
        out.println("end");
        return statistics;
    }

    private static List<String> successRow(TestFile file, TestFile analyzed, Map<String, Integer> smellCounts) {
        List<String> values = metadataValues(file, String.valueOf(file.getNumberOfTestMethods()));
        for (AbstractSmell smell : analyzed.getTestSmells()) {
            if (smell == null) values.add("");
            else {
                int count = smell.getNumberOfSmellyTests();
                values.add(String.valueOf(count));
                smellCounts.put(smell.getSmellName(), smellCounts.get(smell.getSmellName()) + count);
            }
        }
        return values;
    }

    private static void writeFailedRow(ResultsWriter writer, TestFile file, int smellCount) throws IOException {
        List<String> values = metadataValues(file, "0");
        for (int i = 0; i < smellCount; i++) values.add("0");
        writer.writeLine(values);
    }

    private static List<String> metadataValues(TestFile file, String methodCount) {
        List<String> values = new ArrayList<>();
        values.add(file.getApp());
        values.add(file.getTestFileName());
        values.add(file.getTestFilePath());
        values.add(file.getProductionFilePath());
        values.add(file.getRelativeTestFilePath());
        values.add(file.getRelativeProductionFilePath());
        values.add(methodCount);
        return values;
    }

    private static void writeFailures(String outputFile, List<FailureRecord> failures) throws IOException {
        Path outputPath = Paths.get(outputFile);
        String name = outputPath.getFileName().toString();
        String failureName = name.endsWith(".csv") ? name.substring(0, name.length() - 4) + "_failures.csv"
                : name + "_failures.csv";
        Path failurePath = outputPath.resolveSibling(failureName);
        try (Writer writer = Files.newBufferedWriter(failurePath, StandardCharsets.UTF_8);
             CSVWriter csvWriter = new CSVWriter(writer, CSVWriter.DEFAULT_SEPARATOR,
                     CSVWriter.DEFAULT_QUOTE_CHARACTER, CSVWriter.DEFAULT_QUOTE_CHARACTER,
                     CSVWriter.RFC4180_LINE_END)) {
            // First three columns are retained for existing consumers; the final two make location/stage explicit.
            csvWriter.writeNext(new String[]{"InputRow", "TestFilePath", "Reason",
                    "InputStartLine", "FailureStage"});
            for (FailureRecord failure : failures) csvWriter.writeNext(failure.toCsvRow());
        }
    }
}
