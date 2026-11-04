package testsmell;

import com.opencsv.CSVReader;
import com.opencsv.ICSVParser;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Reads detector input while retaining logical-record and physical-line identity. */
public final class InputCsvReader {
    private InputCsvReader() { }

    public static InputData read(Path input) throws IOException {
        List<InputRecord> records = new ArrayList<>();
        boolean headerPresent = false;
        try (Reader reader = Files.newBufferedReader(input, StandardCharsets.UTF_8);
             CSVReader csvReader = new CSVReader(reader, ICSVParser.DEFAULT_SEPARATOR,
                     ICSVParser.DEFAULT_QUOTE_CHARACTER, ICSVParser.NULL_CHARACTER)) {
            long logicalRecordNumber = 0;
            while (true) {
                long startLine = csvReader.getLinesRead() + 1;
                String[] values = csvReader.readNext();
                if (values == null) break;
                if (logicalRecordNumber == 0 && records.isEmpty() && isHeader(values)) {
                    headerPresent = true;
                    continue;
                }
                logicalRecordNumber++;
                records.add(new InputRecord(logicalRecordNumber, startLine, values));
            }
        }
        return new InputData(headerPresent, records);
    }

    private static boolean isHeader(String[] values) {
        return values.length >= 2 && "App".equalsIgnoreCase(values[0].trim())
                && "TestFilePath".equalsIgnoreCase(values[1].trim());
    }

    public static final class InputData {
        private final boolean headerPresent;
        private final List<InputRecord> records;

        private InputData(boolean headerPresent, List<InputRecord> records) {
            this.headerPresent = headerPresent;
            this.records = Collections.unmodifiableList(new ArrayList<>(records));
        }
        public boolean isHeaderPresent() { return headerPresent; }
        public List<InputRecord> getRecords() { return records; }
    }

    public static final class InputRecord {
        private final long recordNumber;
        private final long startLine;
        private final String[] values;

        private InputRecord(long recordNumber, long startLine, String[] values) {
            this.recordNumber = recordNumber;
            this.startLine = startLine;
            this.values = values.clone();
        }
        public long getRecordNumber() { return recordNumber; }
        public long getStartLine() { return startLine; }
        public String[] getValues() { return values.clone(); }
        public boolean isValid() {
            return values.length >= 2 && !values[0].isEmpty() && !values[1].isEmpty();
        }
        public TestFile toTestFile() {
            return new TestFile(values[0], values[1], values.length >= 3 ? values[2] : "");
        }
    }
}
