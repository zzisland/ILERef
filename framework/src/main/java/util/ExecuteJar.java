package util;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.util.ArrayList;
import java.util.List;

public class ExecuteJar {

    public static int runJar(String jarPath, List<String> args) {
        return runJar(jarPath, args, null);
    }

    public static int runJar(String jarPath, List<String> args, String workingDir) {
        List<String> command = new ArrayList<>();
        command.add("java");
        command.add("-Dfile.encoding=UTF-8");
        command.add("-jar");
        command.add(jarPath);
        command.addAll(args);

        System.out.println(String.join(" ", command));

        try {
            ProcessBuilder processBuilder = new ProcessBuilder(command);
            processBuilder.directory(workingDir != null
                    ? new File(workingDir)
                    : new File(System.getProperty("user.dir")));
            Process process = processBuilder.start();

            readStream(process.getInputStream());
            readStream(process.getErrorStream());

            int exitCode = process.waitFor();
            if (exitCode == 0) {
                System.out.println("success");
            } else {
                System.out.println("fail, exitCode: " + exitCode + ", jarPath: " + jarPath);
            }
            return exitCode;
        } catch (IOException | InterruptedException e) {
            e.printStackTrace();
            Thread.currentThread().interrupt();
            return -1;
        }
    }

    private static void readStream(InputStream inputStream) {
        new Thread(() -> {
            BufferedReader reader = new BufferedReader(new InputStreamReader(inputStream));
            String line;
            try {
                while ((line = reader.readLine()) != null) {
                    System.out.println(line);
                }
            } catch (IOException e) {
                e.printStackTrace();
            }
        }).start();
    }
}
