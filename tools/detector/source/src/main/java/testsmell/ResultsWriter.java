package testsmell;

import com.opencsv.CSVWriter;

import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.text.MessageFormat;
import java.util.Calendar;
import java.util.List;

/** Writes UTF-8, standards-compliant CSV detector output. */
public class ResultsWriter {
    private final String outputFile;

    private ResultsWriter() throws IOException {
        this(Paths.get(MessageFormat.format("{0}_{1}_{2}.{3}", "Output", "TestSmellDetection",
                String.valueOf(Calendar.getInstance().getTimeInMillis()), "csv")));
    }

    private ResultsWriter(Path path) throws IOException {
        outputFile = path.toString();
        Files.newBufferedWriter(path, StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING).close();
    }

    public static ResultsWriter createResultsWriter() throws IOException {
        return new ResultsWriter();
    }

    public static ResultsWriter createResultsWriter(Path path) throws IOException {
        return new ResultsWriter(path);
    }

    public void writeColumnName(List<String> columnNames) throws IOException {
        writeOutput(columnNames);
    }

    public void writeLine(List<String> columnValues) throws IOException {
        writeOutput(columnValues);
    }

    private void writeOutput(List<String> dataValues) throws IOException {
        try (Writer writer = Files.newBufferedWriter(Paths.get(outputFile), StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.APPEND);
             CSVWriter csvWriter = new CSVWriter(writer, CSVWriter.DEFAULT_SEPARATOR,
                     CSVWriter.DEFAULT_QUOTE_CHARACTER, CSVWriter.DEFAULT_QUOTE_CHARACTER,
                     CSVWriter.RFC4180_LINE_END)) {
            csvWriter.writeNext(dataValues.toArray(new String[0]));
        }
    }

    public String getOutputFile() {
        return outputFile;
    }
}
