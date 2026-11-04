package service;

import entity.TaskParam;

import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Properties;


public class TaskParamReader {

    public static TaskParam taskParam;

    public static TaskParam getTaskParam() {
        if (taskParam != null) {
            return taskParam;
        }
        taskParam = buildTaskParam();
        return taskParam;
    }

    private static TaskParam buildTaskParam() {
        String projectFilepath = System.getProperty("projectFilepath");
        if (projectFilepath == null || projectFilepath.isBlank()) {
            projectFilepath = System.getenv("UTREFACTOR_PROJECT_FILEPATH");
        }
        if (projectFilepath != null && !projectFilepath.isBlank()) {
            return TaskParam.builder()
                    .projectFilepath(projectFilepath)
                    .build();
        }

        Properties properties = new Properties();
        try (InputStream in = openTaskProperties()) {
            properties.load(in);
        } catch (IOException e) {
            throw new RuntimeException(e);
        }


        return TaskParam.builder()
                .projectFilepath(properties.getProperty("projectFilepath"))
                .build();

    }

    private static InputStream openTaskProperties() throws IOException {
        java.io.File externalFile = new java.io.File("src/main/resources/task.properties");
        if (externalFile.isFile()) {
            return new FileInputStream(externalFile);
        }

        InputStream in = Thread.currentThread()
                .getContextClassLoader()
                .getResourceAsStream("task.properties");
        if (in != null) {
            return in;
        }

        in = TaskParamReader.class.getClassLoader().getResourceAsStream("task.properties");
        if (in != null) {
            return in;
        }

        return new FileInputStream("src/main/resources/task.properties");
    }

}
