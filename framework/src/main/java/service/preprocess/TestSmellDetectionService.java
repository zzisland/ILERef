package service.preprocess;

import entity.TestSmellDetectionOutput;
import entity.TestSmellType;
import org.apache.commons.csv.CSVFormat;
import org.apache.commons.csv.CSVParser;
import org.apache.commons.csv.CSVRecord;
import org.apache.commons.lang3.StringUtils;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class TestSmellDetectionService {

    private static final List<String> IDENTITY_HEADERS = List.of(
            "App",
            "TestClass",
            "TestFilePath",
            "ProductionFilePath",
            "RelativeTestFilePath",
            "RelativeProductionFilePath",
            "NumberOfMethods");

    private static final Map<String, TestSmellType> SMELL_HEADERS = smellHeaders();

    public List<TestSmellDetectionOutput> buildTestSmellDetectionOutputList(String csvFilepath) {
        Path path = Path.of(csvFilepath);
        List<TestSmellDetectionOutput> outputs = new ArrayList<>();
        CSVFormat format = CSVFormat.DEFAULT.builder()
                .setHeader()
                .setSkipHeaderRecord(true)
                .setIgnoreEmptyLines(true)
                .build();
        try (Reader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8);
             CSVParser parser = format.parse(reader)) {
            validateHeaders(path, parser.getHeaderMap());
            for (CSVRecord record : parser) {
                if (!record.isConsistent()) {
                    throw new IllegalStateException(
                            "Malformed detector CSV record " + record.getRecordNumber()
                                    + " in " + path.toAbsolutePath()
                                    + ": expected " + parser.getHeaderMap().size()
                                    + " fields but found " + record.size());
                }
                TestSmellDetectionOutput output = new TestSmellDetectionOutput();
                output.setAppName(record.get("App"));
                output.setTestClassName(record.get("TestClass"));
                output.setTestFilepath(record.get("TestFilePath"));

                List<TestSmellType> smells = new ArrayList<>();
                for (Map.Entry<String, TestSmellType> entry : SMELL_HEADERS.entrySet()) {
                    String value = record.get(entry.getKey());
                    if (StringUtils.isNotBlank(value) && !StringUtils.equals(value.trim(), "0")) {
                        smells.add(entry.getValue());
                    }
                }
                output.setTestSmellTypeList(smells);
                outputs.add(output);
            }
        } catch (IOException exception) {
            throw new IllegalStateException(
                    "Cannot parse detector CSV " + path.toAbsolutePath(), exception);
        }
        return outputs;
    }

    private static void validateHeaders(Path path, Map<String, Integer> headers) {
        List<String> missing = new ArrayList<>();
        for (String header : IDENTITY_HEADERS) {
            if (!headers.containsKey(header)) {
                missing.add(header);
            }
        }
        for (String header : SMELL_HEADERS.keySet()) {
            if (!headers.containsKey(header)) {
                missing.add(header);
            }
        }
        if (!missing.isEmpty()) {
            throw new IllegalStateException(
                    "Detector CSV is missing required header(s) " + missing
                            + ": " + path.toAbsolutePath());
        }
    }

    private static Map<String, TestSmellType> smellHeaders() {
        Map<String, TestSmellType> headers = new LinkedHashMap<>();
        headers.put("Assertion Roulette", TestSmellType.Assertion_Roulette);
        headers.put("Conditional Test Logic", TestSmellType.Conditional_Test_Logic);
        headers.put("Constructor Initialization", TestSmellType.Constructor_Initialization);
        headers.put("Default Test", TestSmellType.Default_Test);
        headers.put("EmptyTest", TestSmellType.EmptyTest);
        headers.put("Exception Catching Throwing", TestSmellType.Exception_Catching_Throwing);
        headers.put("General Fixture", TestSmellType.General_Fixture);
        headers.put("Mystery Guest", TestSmellType.Mystery_Guest);
        headers.put("Print Statement", TestSmellType.Print_Statement);
        headers.put("Redundant Assertion", TestSmellType.Redundant_Assertion);
        headers.put("Sensitive Equality", TestSmellType.Sensitive_Equality);
        headers.put("Verbose Test", TestSmellType.Verbose_Test);
        headers.put("Sleepy Test", TestSmellType.Sleepy_Test);
        headers.put("Eager Test", TestSmellType.Eager_Test);
        headers.put("Lazy Test", TestSmellType.Lazy_Test);
        headers.put("Duplicate Assert", TestSmellType.Duplicate_Assert);
        headers.put("Unknown Test", TestSmellType.Unknown_Test);
        headers.put("IgnoredTest", TestSmellType.IgnoredTest);
        headers.put("Resource Optimism", TestSmellType.Resource_Optimism);
        headers.put("Magic Number Test", TestSmellType.Magic_Number_Test);
        headers.put("Dependent Test", TestSmellType.Dependent_Test);
        return headers;
    }
}
