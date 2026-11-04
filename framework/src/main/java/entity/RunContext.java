package entity;

import util.FileUtil;

import java.io.File;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;

/**
 * Timestamped run context.  All pipeline output for one experiment batch lives
 * under data/{runId}/{projectName}/ with detection, json, skeleton, and
 * refactoring subfolders.
 */
public class RunContext {

    private static final DateTimeFormatter FORMATTER = DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss");
    private static final String DATA_DIR = "data";

    private final String runId;
    private final String batchDir;
    private final String projectDir;
    private final String projectName;

    private RunContext(String runId, String batchDir, String projectDir, String projectName) {
        this.runId = runId;
        this.batchDir = batchDir;
        this.projectDir = projectDir;
        this.projectName = projectName;
    }

    public static RunContext create(String projectName) {
        String runId = System.getenv("UTREFACTOR_RUN_ID");
        if (runId == null || runId.isBlank()) {
            runId = System.getProperty("utrefactor.runId");
        }
        if (runId == null || runId.isBlank()) {
            runId = LocalDateTime.now().format(FORMATTER);
        }
        String batchDir = System.getProperty("user.dir") + File.separator + DATA_DIR + File.separator + runId;
        String projectDir = batchDir + File.separator + projectName;
        return new RunContext(runId, batchDir, projectDir, projectName);
    }

    public String getRunId() {
        return runId;
    }

    /** Project output dir for this batch: data/{runId}/{projectName}. */
    public String getRunDir() {
        return projectDir;
    }

    public String getBatchDir() {
        return batchDir;
    }

    public String getProjectName() {
        return projectName;
    }

    public String getJsonDir() {
        return projectDir + File.separator + "json";
    }

    public String getSkeletonDir() {
        return projectDir + File.separator + "skeleton";
    }

    /** Per-method detector inputs, kept outside the dataset checkout. */
    public String getSplitDir() {
        return projectDir + File.separator + "split";
    }

    public String getDetectionDir() {
        return projectDir + File.separator + "detection";
    }

    public String getRefactoringDir() {
        return projectDir + File.separator + "refactoring";
    }

    public String getDataDir() {
        return System.getProperty("user.dir") + File.separator + DATA_DIR;
    }

    /** Create the run subfolders. */
    public void ensureDirectories() {
        ensureDetectionDirectories();
        new File(getJsonDir()).mkdirs();
        new File(getRefactoringDir()).mkdirs();
    }

    /** Create only the folders required by preprocessing and smell detection. */
    public void ensureDetectionDirectories() {
        new File(getDetectionDir()).mkdirs();
        new File(getSkeletonDir()).mkdirs();
        new File(getSplitDir()).mkdirs();
    }

    /** Write data/latest so the Python refactoring step can find this run. */
    public void writeLatest() {
        FileUtil.writeDataToFile(getDataDir() + File.separator + "latest", runId);
    }
}
