package service.preprocess;

import com.google.common.collect.Lists;
import entity.TaskParam;
import entity.TestContext;
import org.apache.commons.lang3.StringUtils;
import service.TaskParamReader;
import util.FileUtil;
import util.GetFoldFileNames;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.Comparator;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import java.util.regex.Pattern;

public class TestContextService {
    private static final Pattern UNSUPPORTED_RECORD_PATTERN =
            Pattern.compile("(?m)^\\s*(?:public\\s+|private\\s+|protected\\s+|static\\s+|final\\s+)*record\\s+\\w+");
    private static final Pattern UNSUPPORTED_SEALED_PATTERN =
            Pattern.compile("(?m)^\\s*(?:public\\s+|private\\s+|protected\\s+|abstract\\s+)*sealed\\s+");
    private static final Pattern UNSUPPORTED_PERMITS_PATTERN =
            Pattern.compile("\\bpermits\\b");
    private static final Pattern UNSUPPORTED_ANNOTATION_PATTERN =
            Pattern.compile("(?m)^\\s*(?:public\\s+)?@interface\\s+\\w+");
    private static final Pattern PACKAGE_PATTERN =
            Pattern.compile("(?m)^\\s*package\\s+([\\w.]+)\\s*;");

    public static List<String> fetchClassFilepathList() {
        TaskParam taskParam = TaskParamReader.getTaskParam();
        Path projectRoot = Paths.get(taskParam.getProjectFilepath());
        if (!Files.exists(projectRoot)) {
            return Lists.newArrayList();
        }
        return listJavaFiles(projectRoot)
                .filter(path -> normalizePath(path).contains("/src/main/"))
                .map(Path::toAbsolutePath)
                .map(Path::toString)
                .collect(Collectors.toList());
    }

    public static List<String> fetchTestFilepathList() {
        TaskParam taskParam = TaskParamReader.getTaskParam();
        Path projectRoot = Paths.get(taskParam.getProjectFilepath());
        if (Files.exists(projectRoot)) {
            try (Stream<Path> stream = Files.walk(projectRoot)) {
                return stream
                        .filter(Files::isRegularFile)
                        .filter(path -> path.toString().endsWith(".java"))
                        .filter(path -> isAllJavaTestSourceMode()
                                || normalizePath(path).contains("/src/test/java/"))
                        .filter(path -> !normalizePath(path).contains("/src/test_split/"))
                        .filter(path -> !path.getFileName().toString().contains("__"))
                        .filter(path -> !containsUnsupportedDetectorSyntax(path.toFile()))
                        .map(Path::toAbsolutePath)
                        .map(Path::toString)
                        .collect(Collectors.toList());
            } catch (IOException e) {
                return Lists.newArrayList();
            }
        }
        return Lists.newArrayList();
    }

    public static List<TestContext> buildTestContext(String projectName) {
        TaskParam taskParam = TaskParamReader.getTaskParam();
        Path projectRoot = Paths.get(taskParam.getProjectFilepath());
        List<String> classFilepathList = fetchClassFilepathList();
        List<String> testFilepathList = fetchTestFilepathList();
        List<TestContext> testContextList = Lists.newArrayList();
        for (String testFilepath : testFilepathList) {
            String testName = FileUtil.getNameByFilepath(testFilepath);
            if (!testName.startsWith("Test") && !testName.endsWith("Test")) {
                continue;
            }

            TestContext testContext = new TestContext();
            testContext.setProjectName(projectName);
            testContext.setModulePath(resolveModulePath(testFilepath, projectRoot));
            testContext.setTestFilepath(testFilepath);
            testContext.setOriginalTestFilepath(testFilepath);
            testContext.setTestRelativePath(resolveRelativePath(testFilepath, projectRoot));
            testContext.setTestPackageName(readPackageName(testFilepath));
            testContext.setClassFilepath(resolveProductionFilepath(
                    testName,
                    testContext.getTestPackageName(),
                    testContext.getModulePath(),
                    classFilepathList,
                    projectRoot));
            if (containsUnsupportedProductionSyntax(testContext.getClassFilepath())) {
                continue;
            }
            testContextList.add(testContext);
        }
        return testContextList;
    }

    private static Stream<Path> listJavaFiles(Path root) {
        try {
            return Files.walk(root)
                    .filter(Files::isRegularFile)
                    .filter(path -> path.toString().endsWith(".java"));
        } catch (IOException e) {
            return Stream.empty();
        }
    }

    private static boolean isAllJavaTestSourceMode() {
        String mode = System.getProperty("utrefactor.testSourceMode");
        if (StringUtils.isBlank(mode)) {
            mode = System.getenv("UTREFACTOR_TEST_SOURCE_MODE");
        }
        return "all-java".equalsIgnoreCase(mode);
    }

    private static String normalizePath(Path path) {
        return path.toString().replace('\\', '/');
    }

    private static String resolveModulePath(String testFilepath, Path projectRoot) {
        if (StringUtils.isBlank(testFilepath) || projectRoot == null) {
            return StringUtils.EMPTY;
        }
        try {
            Path root = projectRoot.toAbsolutePath().normalize();
            Path testPath = Paths.get(testFilepath).toAbsolutePath().normalize();
            String relativePath = normalizePath(root.relativize(testPath));
            String marker = "/src/test/java/";
            int index = relativePath.indexOf(marker);
            if (index <= 0) {
                return StringUtils.EMPTY;
            }
            return relativePath.substring(0, index);
        } catch (RuntimeException e) {
            return StringUtils.EMPTY;
        }
    }

    private static String resolveRelativePath(String filepath, Path projectRoot) {
        if (StringUtils.isBlank(filepath) || projectRoot == null) {
            return StringUtils.EMPTY;
        }
        try {
            Path root = projectRoot.toAbsolutePath().normalize();
            Path path = Paths.get(filepath).toAbsolutePath().normalize();
            return normalizePath(root.relativize(path));
        } catch (RuntimeException e) {
            return StringUtils.EMPTY;
        }
    }

    private static String readPackageName(String filepath) {
        if (StringUtils.isBlank(filepath)) {
            return StringUtils.EMPTY;
        }
        try {
            java.util.regex.Matcher matcher = PACKAGE_PATTERN.matcher(Files.readString(Paths.get(filepath)));
            return matcher.find() ? matcher.group(1) : StringUtils.EMPTY;
        } catch (IOException | RuntimeException e) {
            return StringUtils.EMPTY;
        }
    }

    private static String resolveProductionFilepath(
            String testName,
            String testPackageName,
            String testModulePath,
            List<String> classFilepathList,
            Path projectRoot) {
        List<String> productionNames = Lists.newArrayList(testName);
        if (testName.startsWith("Test") && testName.length() > 4) {
            productionNames.add(testName.substring(4));
        }
        if (testName.endsWith("Test") && testName.length() > 4) {
            productionNames.add(testName.substring(0, testName.length() - 4));
        }

        return classFilepathList.stream()
                .filter(path -> productionNames.contains(FileUtil.getNameByFilepath(path)))
                .sorted(Comparator
                        .comparingInt((String path) -> productionCandidateScore(
                                path, testPackageName, testModulePath, projectRoot))
                        .reversed()
                        .thenComparing(path -> normalizePath(Paths.get(path))))
                .findFirst()
                .orElse(StringUtils.EMPTY);
    }

    private static int productionCandidateScore(
            String productionFilepath,
            String testPackageName,
            String testModulePath,
            Path projectRoot) {
        int score = 0;
        if (StringUtils.equals(testPackageName, readPackageName(productionFilepath))) {
            score += 100;
        }
        if (StringUtils.equals(testModulePath, resolveProductionModulePath(productionFilepath, projectRoot))) {
            score += 20;
        }
        return score;
    }

    private static String resolveProductionModulePath(String filepath, Path projectRoot) {
        String relativePath = resolveRelativePath(filepath, projectRoot);
        String marker = "/src/main/";
        int index = relativePath.indexOf(marker);
        return index <= 0 ? StringUtils.EMPTY : relativePath.substring(0, index);
    }



    private static boolean containsUnsupportedDetectorSyntax(File file) {
        try {
            String source = Files.readString(file.toPath());
            return UNSUPPORTED_RECORD_PATTERN.matcher(source).find()
                    || UNSUPPORTED_SEALED_PATTERN.matcher(source).find()
                    || UNSUPPORTED_PERMITS_PATTERN.matcher(source).find();
        } catch (IOException e) {
            return true;
        }
    }

    private static boolean containsUnsupportedProductionSyntax(String filepath) {
        if (StringUtils.isBlank(filepath)) {
            return false;
        }
        try {
            String source = Files.readString(new File(filepath).toPath());
            return UNSUPPORTED_RECORD_PATTERN.matcher(source).find()
                    || UNSUPPORTED_SEALED_PATTERN.matcher(source).find()
                    || UNSUPPORTED_PERMITS_PATTERN.matcher(source).find()
                    || UNSUPPORTED_ANNOTATION_PATTERN.matcher(source).find();
        } catch (IOException e) {
            return true;
        }
    }
}

