package service.detection;

import com.google.common.collect.Lists;
import entity.RunContext;
import entity.TestContext;
import entity.TestSmellDetectionOutput;
import org.apache.commons.collections.CollectionUtils;
import org.apache.commons.lang3.StringUtils;
import service.preprocess.TestContextService;
import service.preprocess.TestFileSplitService;
import service.preprocess.TestSmellDetectionService;
import util.ExecuteJar;

import java.io.File;
import java.io.IOException;
import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.*;

public class TestSmellDetection {

    public List<TestSmellDetectionOutput> smellDetect(String projectName, RunContext runContext) {
        // collect
        List<TestContext> testContexts = TestContextService.buildTestContext(projectName);
        System.out.println("[stats] originalTestFiles=" + testContexts.size());

        // split
        TestFileSplitService testFileSplitService = new TestFileSplitService();
        testFileSplitService.writeSkeletons(testContexts, projectName, runContext.getSkeletonDir());
        System.out.println("[stats] skeletonFiles=" + testContexts.size());
        List<TestContext> splitTestContexts = testFileSplitService.splitTest(testContexts, runContext.getSplitDir());
        System.out.println("[stats] splitTestContexts=" + splitTestContexts.size());

        String detectionDir = runContext.getDetectionDir();
        new File(detectionDir).mkdirs();

        String testCSVFilepath = writeToTestCSV(splitTestContexts, detectionDir);
        System.out.println("testCSVFilepath:" + testCSVFilepath);
        if (StringUtils.isBlank(testCSVFilepath)) {
            throw new IllegalStateException("No test contexts were found. Please check projectFilepath and target project test sources.");
        }

        String jarFilepath = resolveDetectorJarPath();
        List<String> detectArgs = Lists.newArrayList();
        detectArgs.add(testCSVFilepath);

        long detectStartTime = System.currentTimeMillis();
        // Keep CWD at repo root because detector input may contain production paths
        // relative to the repository (for example datasets/cli/src/main/...).
        int exitCode = ExecuteJar.runJar(jarFilepath, detectArgs, System.getProperty("user.dir"));
        if (exitCode != 0) {
            throw new IllegalStateException("Test smell detector failed, exitCode=" + exitCode);
        }
        moveLatestDetectionResult(detectStartTime, System.getProperty("user.dir"), detectionDir);

        return buildSmellDetection(splitTestContexts, detectStartTime, detectionDir);
    }

    static String resolveDetectorJarPath() {
        String configuredPath = System.getProperty("tsdetect.jar");
        if (StringUtils.isBlank(configuredPath)) {
            configuredPath = System.getenv("UTREFACTOR_TSDETECT_JAR");
        }
        if (StringUtils.isBlank(configuredPath)) {
            configuredPath = "detection/junit5/TestSmellDetector.jar";
        }
        Path path = Path.of(configuredPath);
        if (!path.isAbsolute()) {
            path = Path.of(System.getProperty("user.dir")).resolve(path);
        }
        return path.normalize().toString();
    }

    private void moveLatestDetectionResult(long minLastModifiedMillis, String sourceDir, String detectionDir) {
        String latestResult = getLastSmellDetectResult(minLastModifiedMillis, sourceDir);
        if (latestResult == null) {
            throw new IllegalStateException("Test smell detector did not generate Output_TestSmellDetection_*.csv");
        }
        try {
            File sourceFile = new File(latestResult);
            Path targetPath = Path.of(detectionDir, sourceFile.getName());
            Files.createDirectories(targetPath.getParent());
            Files.move(sourceFile.toPath(), targetPath, StandardCopyOption.REPLACE_EXISTING);

            String baseName = sourceFile.getName();
            String failureName = baseName.substring(0, baseName.length() - ".csv".length()) + "_failures.csv";
            Path failureSource = Path.of(sourceDir, failureName);
            if (Files.isRegularFile(failureSource)) {
                Files.move(failureSource, Path.of(detectionDir, failureName), StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (IOException e) {
            throw new IllegalStateException("Could not move test smell detection result to " + detectionDir, e);
        }
    }

    private List<TestSmellDetectionOutput> buildSmellDetection(List<TestContext> splitTestContexts,
                                                               long detectStartTime, String detectionDir) {
        String lastSmellDetectResult = getLastSmellDetectResult(detectStartTime, detectionDir);
        if (lastSmellDetectResult == null) {
            throw new IllegalStateException("Test smell detector did not generate Output_TestSmellDetection_*.csv");
        }
        TestSmellDetectionService testSmellDetectionService = new TestSmellDetectionService();
        List<TestSmellDetectionOutput> testSmellDetectionOutputs = testSmellDetectionService.buildTestSmellDetectionOutputList(lastSmellDetectResult);

        testSmellDetectionOutputs.forEach(x -> {
            TestContext testContext = splitTestContexts.stream()
                    .filter(context -> StringUtils.equals(x.getTestFilepath(), context.getTestFilepath()))
                    .findFirst().orElse(null);
            if (testContext == null) {
                return;
            }
            if (StringUtils.isNotBlank(testContext.getClassFilepath())) {
                x.setClassFilepath(testContext.getClassFilepath());
            }
            x.setModulePath(testContext.getModulePath());
            x.setOriginalTestFilepath(testContext.getOriginalTestFilepath());
            x.setTestRelativePath(testContext.getTestRelativePath());
            x.setTestPackageName(testContext.getTestPackageName());
            x.setTestOriginalCode(testContext.getTestOriginalCode());
            x.setTestCodeStartLine(testContext.getTestCodeStartLine());
            x.setTestCodeEndLine(testContext.getTestCodeEndLine());
            x.setMethodCtx(testContext.getMethodCtx());
            x.setSplitFilename(testContext.getSplitFileName());
        });

        return testSmellDetectionOutputs;
    }

    public String getLastSmellDetectResult(long minLastModifiedMillis, String detectionDir) {
        String fileNamePrefix = "Output_TestSmellDetection_";
        String fileExtension = ".csv";

        File directory = new File(detectionDir);
        File[] files = directory.listFiles(file -> {
            String name = file.getName();
            return name.startsWith(fileNamePrefix)
                    && name.endsWith(fileExtension)
                    && !name.endsWith("_failures.csv")
                    && file.lastModified() >= minLastModifiedMillis;
        });

        if (files != null && files.length > 0) {
            List<File> fileList = new ArrayList<>(Arrays.asList(files));
            fileList.sort(Comparator.comparing(File::getName));

            File lastFile = fileList.get(fileList.size() - 1);
            return lastFile.getAbsolutePath();
        }

        return null;
    }

    private static String writeToTestCSV(List<TestContext> testContextList, String detectionDir) {
        if (CollectionUtils.isEmpty(testContextList)) {
            return null;
        }
        String csvFilepath = detectionDir + "/detector_input_"
                + testContextList.get(0).getProjectName() + ".csv";
        File file = new File(csvFilepath);
        File parentDir = file.getParentFile();
        if (parentDir != null && !parentDir.exists()) {
            parentDir.mkdirs();
        }
        try (BufferedWriter writer = Files.newBufferedWriter(file.toPath(), StandardCharsets.UTF_8)) {
            int writtenCount = 0;
            for (TestContext testContext : testContextList) {
                String stringBuilder = testContext.getProjectName() +
                        "," +
                        testContext.getTestFilepath() +
                        "," +
                        testContext.getClassFilepath() +
                        "\n";
                writer.write(stringBuilder);
                writtenCount++;
            }
            System.out.println("[stats] detectorCsvRows=" + writtenCount);
            if (writtenCount == 0) {
                return null;
            }
        } catch (IOException e) {
            e.printStackTrace();
        }

        return csvFilepath;
    }
}
